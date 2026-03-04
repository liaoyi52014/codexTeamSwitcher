"""Usage Monitor Service for tracking team quota usage."""

import time
from datetime import datetime
from typing import Callable, Optional, List
from dataclasses import dataclass

from src.models.team import Team
from src.services.codex_client import CodexClient, UsageInfo, StatusCommandError
from src.services.token_manager import TokenManager
from src.utils.logger import get_logger, log_usage_check


@dataclass
class UsageCheckResult:
    """Result of a usage check operation."""

    team_id: str
    success: bool
    usage: Optional[UsageInfo]
    error: Optional[str]
    timestamp: datetime


class UsageMonitor:
    """
    Monitors team quota usage by polling /status command.

    Periodically checks the current team's usage and triggers
    callbacks when quota falls below the threshold.
    """

    def __init__(
        self,
        token_manager: TokenManager,
        codex_client: Optional[CodexClient] = None,
        threshold_percentage: float = 5.0,
        check_interval_seconds: int = 300,
    ):
        """
        Initialize the Usage Monitor.

        Args:
            token_manager: TokenManager instance for accessing team tokens.
            codex_client: Optional CodexClient (created if not provided).
            threshold_percentage: Quota percentage threshold for alerts.
            check_interval_seconds: How often to check usage (in seconds).
        """
        self._token_manager = token_manager
        self._codex_client = codex_client or CodexClient()
        self._threshold = threshold_percentage
        self._interval = check_interval_seconds
        self._logger = get_logger(__name__)

        # Callback functions for events
        self._on_quota_low: Optional[Callable[[Team, UsageInfo], None]] = None
        self._on_check_complete: Optional[Callable[[UsageCheckResult], None]] = None
        self._on_all_teams_checked: Optional[Callable[[List[UsageCheckResult]], None]] = None

    def set_all_teams_checked_callback(
        self, callback: Callable[[List[UsageCheckResult]], None]
    ) -> None:
        """
        Set callback for when all teams have been checked.

        Args:
            callback: Function to call with list of UsageCheckResult.
        """
        self._on_all_teams_checked = callback

    def set_quota_low_callback(
        self, callback: Callable[[Team, UsageInfo], None]
    ) -> None:
        """
        Set callback for when quota falls below threshold.

        Args:
            callback: Function to call with (team, usage_info).
        """
        self._on_quota_low = callback

    def set_check_complete_callback(
        self, callback: Callable[[UsageCheckResult], None]
    ) -> None:
        """
        Set callback for when a check completes (success or failure).

        Args:
            callback: Function to call with UsageCheckResult.
        """
        self._on_check_complete = callback

    def check_team_usage(self, team: Team) -> UsageCheckResult:
        """
        Check the usage for a specific team.

        Args:
            team: Team object to check.

        Returns:
            UsageCheckResult with usage information or error.
        """
        self._logger.info("checking_usage", team_id=team.id, team_name=team.name)

        try:
            # Get decrypted token
            token = self._token_manager.get_decrypted_token(team.id)

            # Execute status command
            usage = self._codex_client.get_usage(token, team_id=team.id)

            # Update quota in database
            self._token_manager.update_quota(
                team_id=team.id,
                quota_total=usage.total,
                quota_used=usage.used,
                quota_remaining=usage.remaining,
                usage_5h_percent=usage.usage_5h_percent,
                usage_weekly_percent=usage.usage_weekly_percent,
            )

            # Log the usage check
            log_usage_check(
                self._logger,
                team_id=team.id,
                quota_percentage=usage.percentage,
                threshold=self._threshold,
            )

            # Check if quota is low and trigger callback
            if usage.percentage < self._threshold:
                self._logger.warning(
                    "quota_below_threshold",
                    team_id=team.id,
                    percentage=usage.percentage,
                    threshold=self._threshold,
                )
                if self._on_quota_low:
                    self._on_quota_low(team, usage)

            result = UsageCheckResult(
                team_id=team.id,
                success=True,
                usage=usage,
                error=None,
                timestamp=datetime.utcnow(),
            )

            if self._on_check_complete:
                self._on_check_complete(result)

            return result

        except StatusCommandError as e:
            self._logger.error(
                "status_command_failed",
                team_id=team.id,
                error=str(e),
            )
            result = UsageCheckResult(
                team_id=team.id,
                success=False,
                usage=None,
                error=str(e),
                timestamp=datetime.utcnow(),
            )
            if self._on_check_complete:
                self._on_check_complete(result)
            return result

        except Exception as e:
            self._logger.error(
                "usage_check_failed",
                team_id=team.id,
                error=str(e),
            )
            result = UsageCheckResult(
                team_id=team.id,
                success=False,
                usage=None,
                error=str(e),
                timestamp=datetime.utcnow(),
            )
            if self._on_check_complete:
                self._on_check_complete(result)
            return result

    def check_active_team_usage(self) -> UsageCheckResult:
        """
        Check usage for the currently active team.

        Returns:
            UsageCheckResult for the active team.
        """
        team = self._token_manager.get_active_team()
        if not team:
            return UsageCheckResult(
                team_id="",
                success=False,
                usage=None,
                error="No active team available",
                timestamp=datetime.utcnow(),
            )

        return self.check_team_usage(team)

    def check_all_teams_usage(self) -> List[UsageCheckResult]:
        """
        Check usage for all enabled teams.

        Returns:
            List of UsageCheckResult for each team.
        """
        teams = self._token_manager.get_enabled_teams()
        results = []

        for team in teams:
            result = self.check_team_usage(team)
            results.append(result)

            # Small delay between checks to avoid rate limiting
            time.sleep(1)

        return results

    def run_single_check(self) -> UsageCheckResult:
        """
        Run a single usage check for the active team.

        This is useful for manual triggers or one-off checks.

        Returns:
            UsageCheckResult for the check.
        """
        return self.check_active_team_usage()

    def start_monitoring(
        self,
        duration_seconds: Optional[int] = None,
        stop_event: Optional[object] = None,
    ) -> None:
        """
        Start continuous usage monitoring.

        Args:
            duration_seconds: Optional duration to run (runs indefinitely if None).
            stop_event: Optional threading.Event to signal stopping.
        """
        self._logger.info(
            "starting_monitoring",
            interval=self._interval,
            threshold=self._threshold,
        )

        start_time = time.time()

        while True:
            # Check if we should stop
            if stop_event and stop_event.is_set():
                self._logger.info("monitoring_stopped")
                break

            # Run check for all enabled teams
            results = self.check_all_teams_usage()

            # Log summary
            success_count = sum(1 for r in results if r.success)
            self._logger.info(
                "all_teams_checked",
                total=len(results),
                success=success_count,
                failed=len(results) - success_count,
            )

            # Trigger callback for all teams checked (for WebSocket push)
            if self._on_all_teams_checked:
                self._on_all_teams_checked(results)

            # Check duration limit
            if duration_seconds and (time.time() - start_time) >= duration_seconds:
                self._logger.info("monitoring_duration_reached")
                break

            # Wait for next interval
            # Use smaller sleep increments to check stop_event more frequently
            for _ in range(self._interval):
                if stop_event and stop_event.is_set():
                    break
                time.sleep(1)

    @property
    def threshold(self) -> float:
        """Get the current threshold percentage."""
        return self._threshold

    @threshold.setter
    def threshold(self, value: float) -> None:
        """Set the threshold percentage."""
        self._threshold = value

    @property
    def check_interval(self) -> int:
        """Get the current check interval in seconds."""
        return self._interval

    @check_interval.setter
    def check_interval(self, value: int) -> None:
        """Set the check interval in seconds."""
        self._interval = value
