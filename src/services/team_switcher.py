"""Team Switcher Service for executing team switching logic."""

import time
from datetime import datetime
from typing import Callable, List, Optional

from sqlalchemy.orm import Session

from src.models.team import Team
from src.models.switch_log import SwitchLog, SwitchReason
from src.services.token_manager import TokenManager, TokenExpiredError
from src.services.codex_client import CodexClient, StatusCommandError
from src.utils.logger import get_logger, log_team_switch


class SwitchError(Exception):
    """Base exception for switch errors."""
    pass


class NoAvailableTeamError(SwitchError):
    """Raised when no team is available for switching."""
    pass


class SwitchValidationError(SwitchError):
    """Raised when switch validation fails."""
    pass


class TeamSwitcher:
    """
    Manages team switching operations.

    Handles the logic for switching between teams when quota
    is low or other conditions trigger a switch.
    """

    def __init__(
        self,
        token_manager: TokenManager,
        db_session: Session,
        codex_client: Optional[CodexClient] = None,
        threshold_percentage: float = 5.0,
    ):
        """
        Initialize the Team Switcher.

        Args:
            token_manager: TokenManager instance for team operations.
            db_session: Database session for logging switches.
            codex_client: Optional CodexClient (created if not provided).
            threshold_percentage: Quota threshold for triggering switches.
        """
        self._token_manager = token_manager
        self._db = db_session
        self._codex_client = codex_client or CodexClient()
        self._threshold = threshold_percentage
        self._logger = get_logger(__name__)

        # Current team ID
        self._current_team_id: Optional[str] = None

        # Callbacks
        self._on_switch_complete: Optional[Callable[[Team, Team, bool], None]] = None

        # Track if we're currently switching
        self._is_switching = False

    def set_switch_complete_callback(
        self, callback: Callable[[Team, Team, bool], None]
    ) -> None:
        """
        Set callback for when a switch completes.

        Args:
            callback: Function to call with (from_team, to_team, success).
        """
        self._on_switch_complete = callback

    def get_current_team(self) -> Optional[Team]:
        """
        Get the currently active team.

        Returns:
            Current Team object, or None if no team is active.
        """
        if self._current_team_id:
            return self._token_manager.get_team_by_id(self._current_team_id)
        return self._token_manager.get_active_team()

    def set_current_team(self, team_id: str) -> None:
        """
        Manually set the current team.

        Args:
            team_id: Team to set as current.
        """
        self._current_team_id = team_id

    def get_next_available_team(
        self,
        exclude_teams: Optional[List[str]] = None,
    ) -> Optional[Team]:
        """
        Get the next available team for switching.

        Args:
            exclude_teams: List of team IDs to exclude from selection.

        Returns:
            Next available Team, or None if no team is available.
        """
        exclude_teams = exclude_teams or []

        # Get all enabled teams sorted by priority
        teams = self._token_manager.get_enabled_teams()

        for team in teams:
            # Skip excluded teams
            if team.id in exclude_teams:
                continue

            # Skip current team
            if self._current_team_id and team.id == self._current_team_id:
                continue

            # Check if team has valid token
            if not self._token_manager.validate_token(team.id):
                self._logger.warning(
                    "skipping_team_invalid_token",
                    team_id=team.id,
                )
                continue

            # Check if team has sufficient quota
            if team.quota_percentage and team.quota_percentage < self._threshold:
                self._logger.warning(
                    "skipping_team_low_quota",
                    team_id=team.id,
                    quota=team.quota_percentage,
                )
                continue

            return team

        return None

    def switch_to_team(
        self,
        target_team_id: str,
        reason: str = SwitchReason.QUOTA_LOW,
        validate: bool = True,
    ) -> bool:
        """
        Switch to a target team.

        Args:
            target_team_id: ID of team to switch to.
            reason: Reason for the switch.
            validate: Whether to validate the target team before switching.

        Returns:
            True if switch successful.

        Raises:
            NoAvailableTeamError: If target team doesn't exist.
            SwitchValidationError: If validation fails.
            SwitchError: If switch fails.
        """
        if self._is_switching:
            raise SwitchError("Switch already in progress")

        self._is_switching = True
        start_time = time.time()

        try:
            # Get current team
            current_team = self.get_current_team()
            current_team_id = current_team.id if current_team else None

            # Get target team
            target_team = self._token_manager.get_team_by_id(target_team_id)
            if not target_team:
                raise NoAvailableTeamError(f"Target team not found: {target_team_id}")

            # Validate target team if requested
            if validate:
                self._validate_target_team(target_team)

            # Perform the switch
            self._logger.info(
                "switching_team",
                from_team=current_team_id,
                to_team=target_team_id,
                reason=reason,
            )

            # Update current team
            self._current_team_id = target_team_id

            # Calculate duration
            duration_ms = int((time.time() - start_time) * 1000)

            # Log successful switch
            self._log_switch(
                from_team_id=current_team_id or "none",
                to_team_id=target_team_id,
                reason=reason,
                success=True,
                from_quota_percentage=current_team.quota_percentage if current_team else None,
                switch_duration_ms=duration_ms,
            )

            log_team_switch(
                self._logger,
                from_team=current_team_id or "none",
                to_team=target_team_id,
                reason=reason,
                success=True,
            )

            # Trigger callback
            if self._on_switch_complete:
                self._on_switch_complete(current_team, target_team, True)

            return True

        except (NoAvailableTeamError, SwitchValidationError) as e:
            self._handle_switch_error(e, current_team, reason)

        finally:
            self._is_switching = False

    def switch_to_next_team(
        self,
        reason: str = SwitchReason.QUOTA_LOW,
        exclude_current: bool = True,
    ) -> bool:
        """
        Switch to the next available team based on priority.

        Args:
            reason: Reason for the switch.
            exclude_current: Whether to exclude current team from selection.

        Returns:
            True if switch successful.

        Raises:
            NoAvailableTeamError: If no team is available.
        """
        exclude_teams = [self._current_team_id] if exclude_current and self._current_team_id else []

        target_team = self.get_next_available_team(exclude_teams=exclude_teams)

        if not target_team:
            raise NoAvailableTeamError(
                f"No available team to switch to. Excluded: {exclude_teams}"
            )

        return self.switch_to_team(target_team.id, reason=reason)

    def _validate_target_team(self, team: Team) -> None:
        """
        Validate that a target team is suitable for switching.

        Args:
            team: Team to validate.

        Raises:
            SwitchValidationError: If validation fails.
        """
        # Check if team is enabled
        if not team.enabled:
            raise SwitchValidationError(f"Team is disabled: {team.id}")

        # Check if token is valid
        if not self._token_manager.validate_token(team.id):
            raise SwitchValidationError(f"Team token is invalid: {team.id}")

        # Check if token is expired
        if team.is_token_expired:
            raise SwitchValidationError(f"Team token is expired: {team.id}")

        # Check quota if available
        if team.quota_percentage and team.quota_percentage < self._threshold:
            raise SwitchValidationError(
                f"Team quota too low: {team.quota_percentage:.1f}% < {self._threshold}%"
            )

        # Verify we can actually access the team
        try:
            token = self._token_manager.get_decrypted_token(team.id)
            usage = self._codex_client.get_usage(token, timeout=10)

            if usage.percentage < self._threshold:
                raise SwitchValidationError(
                    f"Team has insufficient quota: {usage.percentage:.1f}%"
                )
        except StatusCommandError as e:
            raise SwitchValidationError(f"Failed to verify team: {e}")

    def _handle_switch_error(
        self,
        error: Exception,
        current_team: Optional[Team],
        reason: str,
    ) -> None:
        """
        Handle switch error by logging and triggering callback.

        Args:
            error: The error that occurred.
            current_team: The current team before the failed switch.
            reason: The original reason for the switch.
        """
        duration_ms = 0

        self._log_switch(
            from_team_id=current_team.id if current_team else "none",
            to_team_id="none",
            reason=reason,
            success=False,
            from_quota_percentage=current_team.quota_percentage if current_team else None,
            error_message=str(error),
            switch_duration_ms=duration_ms,
        )

        log_team_switch(
            self._logger,
            from_team=current_team.id if current_team else "none",
            to_team="none",
            reason=reason,
            success=False,
        )

        if self._on_switch_complete and current_team:
            self._on_switch_complete(current_team, None, False)

    def _log_switch(
        self,
        from_team_id: str,
        to_team_id: str,
        reason: str,
        success: bool,
        from_quota_percentage: Optional[float] = None,
        error_message: Optional[str] = None,
        switch_duration_ms: Optional[int] = None,
    ) -> None:
        """
        Log a switch event to the database.

        Args:
            from_team_id: Source team ID.
            to_team_id: Destination team ID.
            reason: Switch reason.
            success: Whether switch succeeded.
            from_quota_percentage: Quota percentage before switch.
            error_message: Error message if failed.
            switch_duration_ms: Duration of switch in milliseconds.
        """
        log_entry = SwitchLog(
            timestamp=datetime.utcnow(),
            from_team_id=from_team_id,
            to_team_id=to_team_id,
            reason=reason,
            from_quota_percentage=from_quota_percentage,
            success=success,
            error_message=error_message,
            switch_duration_ms=switch_duration_ms,
        )

        self._db.add(log_entry)
        self._db.commit()

    def get_switch_history(
        self,
        limit: int = 10,
        team_id: Optional[str] = None,
    ) -> List[SwitchLog]:
        """
        Get recent switch history.

        Args:
            limit: Maximum number of entries to return.
            team_id: Optional filter by team ID.

        Returns:
            List of SwitchLog entries.
        """
        query = self._db.query(SwitchLog).order_by(SwitchLog.timestamp.desc())

        if team_id:
            query = query.filter(
                (SwitchLog.from_team_id == team_id) | (SwitchLog.to_team_id == team_id)
            )

        return query.limit(limit).all()

    @property
    def is_switching(self) -> bool:
        """Check if a switch is currently in progress."""
        return self._is_switching
