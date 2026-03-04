"""Token Manager Service for managing OAuth tokens."""

import json
from datetime import datetime
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from cryptography.fernet import InvalidToken

from src.models.team import Team
from src.utils.crypto import TokenEncryptor
from src.utils.codex_auth import (
    extract_codex_auth,
    load_codex_auth_json,
    switch_codex_account,
    is_codex_logged_in,
    get_organization_name,
)
from src.utils.logger import get_logger


class TokenManagerError(Exception):
    """Base exception for Token Manager errors."""
    pass


class TokenNotFoundError(TokenManagerError):
    """Raised when a token is not found."""
    pass


class TokenExpiredError(TokenManagerError):
    """Raised when a token has expired."""
    pass


class TokenValidationError(TokenManagerError):
    """Raised when token validation fails."""
    pass


class TokenManager:
    """
    Manages OAuth tokens for multiple Codex teams.

    Handles storage, encryption, validation, and refresh of team tokens.
    All tokens are encrypted at rest using Fernet symmetric encryption.
    """

    def __init__(self, db_session: Session, encryption_key: str):
        """
        Initialize the Token Manager.

        Args:
            db_session: SQLAlchemy database session.
            encryption_key: Base64 encoded encryption key for token storage.
        """
        self._db = db_session
        self._encryptor = TokenEncryptor(encryption_key)
        self._logger = get_logger(__name__)

    def get_all_teams(self) -> List[Team]:
        """
        Get all teams from the database.

        Returns:
            List of all Team objects.
        """
        return self._db.query(Team).order_by(Team.priority).all()

    def get_enabled_teams(self) -> List[Team]:
        """
        Get all enabled teams ordered by priority.

        Returns:
            List of enabled Team objects sorted by priority.
        """
        return (
            self._db.query(Team)
            .filter(Team.enabled == True)
            .order_by(Team.priority)
            .all()
        )

    def get_team_by_id(self, team_id: str) -> Optional[Team]:
        """
        Get a team by its ID.

        Args:
            team_id: Team identifier.

        Returns:
            Team object if found, None otherwise.
        """
        return self._db.query(Team).filter(Team.id == team_id).first()

    def get_active_team(self) -> Optional[Team]:
        """
        Get the currently active team (first enabled team by priority).

        Returns:
            The highest priority enabled team, or None if no teams available.
        """
        teams = self.get_enabled_teams()
        return teams[0] if teams else None

    def add_team(
        self,
        team_id: str,
        name: str,
        access_token: str,
        refresh_token: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        organization_id: Optional[str] = None,
        priority: int = 1,
        status_command: str = "/status",
    ) -> Team:
        """
        Add a new team with OAuth credentials.

        Args:
            team_id: Unique identifier for the team.
            name: Human-readable team name.
            access_token: OAuth access token (will be encrypted).
            refresh_token: Optional OAuth refresh token.
            expires_at: Optional token expiration time.
            organization_id: Optional organization ID.
            priority: Team priority (lower = higher priority).
            status_command: Custom status command.

        Returns:
            Created Team object.
        """
        # Encrypt tokens before storage
        encrypted_access = self._encryptor.encrypt(access_token)
        encrypted_refresh = (
            self._encryptor.encrypt(refresh_token) if refresh_token else None
        )

        team = Team(
            id=team_id,
            name=name,
            access_token=encrypted_access,
            refresh_token=encrypted_refresh,
            expires_at=expires_at,
            organization_id=organization_id,
            priority=priority,
            status_command=status_command,
            enabled=True,
        )

        self._db.add(team)
        self._db.commit()

        self._logger.info("team_added", team_id=team_id, name=name, priority=priority)
        return team

    def update_team_token(
        self,
        team_id: str,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        expires_at: Optional[datetime] = None,
    ) -> Team:
        """
        Update a team's OAuth tokens.

        Args:
            team_id: Team identifier.
            access_token: New access token (optional).
            refresh_token: New refresh token (optional).
            expires_at: New expiration time (optional).

        Returns:
            Updated Team object.

        Raises:
            TokenNotFoundError: If team doesn't exist.
        """
        team = self.get_team_by_id(team_id)
        if not team:
            raise TokenNotFoundError(f"Team not found: {team_id}")

        if access_token:
            team.access_token = self._encryptor.encrypt(access_token)
        if refresh_token:
            team.refresh_token = self._encryptor.encrypt(refresh_token)
        if expires_at:
            team.expires_at = expires_at

        self._db.commit()
        self._logger.info("team_token_updated", team_id=team_id)

        return team

    def delete_team(self, team_id: str) -> bool:
        """
        Delete a team from the database.

        Args:
            team_id: Team identifier.

        Returns:
            True if deleted, False if not found.
        """
        team = self.get_team_by_id(team_id)
        if not team:
            return False

        self._db.delete(team)
        self._db.commit()

        self._logger.info("team_deleted", team_id=team_id)
        return True

    def get_decrypted_token(self, team_id: str) -> str:
        """
        Get the decrypted access token for a team.

        Args:
            team_id: Team identifier.

        Returns:
            Decrypted access token.

        Raises:
            TokenNotFoundError: If team doesn't exist.
            TokenExpiredError: If token has expired.
            TokenValidationError: If token cannot be decrypted.
        """
        team = self.get_team_by_id(team_id)
        if not team:
            raise TokenNotFoundError(f"Team not found: {team_id}")

        # Check expiration
        if team.is_token_expired:
            raise TokenExpiredError(f"Token expired for team: {team_id}")

        # Decrypt token
        try:
            return self._encryptor.decrypt(team.access_token)
        except InvalidToken as e:
            raise TokenValidationError(f"Failed to decrypt token: {e}")

    def validate_token(self, team_id: str) -> bool:
        """
        Validate a team's token.

        Args:
            team_id: Team identifier.

        Returns:
            True if token is valid, False otherwise.
        """
        try:
            # Try to decrypt the token
            self.get_decrypted_token(team_id)
            return True
        except (TokenExpiredError, TokenValidationError, TokenNotFoundError):
            return False

    def set_team_enabled(self, team_id: str, enabled: bool) -> bool:
        """
        Enable or disable a team.

        Args:
            team_id: Team identifier.
            enabled: Whether the team should be enabled.

        Returns:
            True if updated, False if team not found.
        """
        team = self.get_team_by_id(team_id)
        if not team:
            return False

        team.enabled = enabled
        self._db.commit()

        self._logger.info("team_status_changed", team_id=team_id, enabled=enabled)
        return True

    def update_quota(
        self,
        team_id: str,
        quota_total: int,
        quota_used: int,
        quota_remaining: int,
        usage_5h_percent: float = 100.0,
        usage_weekly_percent: float = 100.0,
    ) -> Team:
        """
        Update a team's quota information.

        Args:
            team_id: Team identifier.
            quota_total: Total quota.
            quota_used: Used quota.
            quota_remaining: Remaining quota.
            usage_5h_percent: 5-hour window remaining percentage.
            usage_weekly_percent: Weekly remaining percentage.

        Returns:
            Updated Team object.

        Raises:
            TokenNotFoundError: If team doesn't exist.
        """
        team = self.get_team_by_id(team_id)
        if not team:
            raise TokenNotFoundError(f"Team not found: {team_id}")

        team.quota_total = quota_total
        team.quota_used = quota_used
        team.quota_remaining = quota_remaining

        # Calculate percentage
        if quota_total > 0:
            team.quota_percentage = (quota_remaining / quota_total) * 100
        else:
            team.quota_percentage = 0.0

        # Store 5h and weekly percentages
        team.quota_5h_percentage = usage_5h_percent
        team.quota_weekly_percentage = usage_weekly_percent

        team.quota_last_checked = datetime.utcnow()
        self._db.commit()

        return team

    def get_teams_by_status(self) -> Dict[str, List[Team]]:
        """
        Categorize teams by their current status.

        Returns:
            Dictionary with keys: 'active', 'quota_low', 'expired', 'disabled'
        """
        teams = self.get_all_teams()
        result = {
            "active": [],
            "quota_low": [],
            "expired": [],
            "disabled": [],
        }

        for team in teams:
            if not team.enabled:
                result["disabled"].append(team)
            elif team.is_token_expired:
                result["expired"].append(team)
            elif team.is_quota_low:
                result["quota_low"].append(team)
            else:
                result["active"].append(team)

        return result

    def import_current_codex_account(self, name: Optional[str] = None) -> Optional[Team]:
        """
        Import the currently logged in Codex account as a team.

        Reads the current auth.json from ~/.codex and stores it
        as a team that can be switched to.

        Args:
            name: Optional custom name for the team. If not provided,
                  uses email or account_id.

        Returns:
            Created Team object, or None if not logged in.
        """
        # Check if Codex is logged in
        if not is_codex_logged_in():
            self._logger.warning("codex_not_logged_in")
            return None

        # Get auth info
        auth = extract_codex_auth()
        if not auth:
            self._logger.warning("failed_to_extract_auth")
            return None

        # Get full auth_json
        auth_json = load_codex_auth_json()
        if not auth_json:
            self._logger.warning("failed_to_load_auth_json")
            return None

        # Determine team name and ID based on organization
        # Use organization_id for unique identification if available
        if auth.organization_id:
            team_id = f"team-{auth.organization_id}"
        else:
            team_id = f"team-{auth.account_id[:8]}"

        # Use team_id as the display name
        team_name = team_id

        # Check if team already exists
        existing = self.get_team_by_id(team_id)
        if existing:
            # Update existing team
            existing.name = team_name
            existing.set_auth_json(auth_json)
            existing.access_token = self._encryptor.encrypt(auth.access_token)
            if auth.refresh_token:
                existing.refresh_token = self._encryptor.encrypt(auth.refresh_token)
            self._db.commit()
            self._logger.info("team_updated", team_id=team_id, name=team_name)
            return existing

        # Create new team
        team = Team(
            id=team_id,
            name=team_name,
            access_token=self._encryptor.encrypt(auth.access_token),
            refresh_token=self._encryptor.encrypt(auth.refresh_token) if auth.refresh_token else None,
            organization_id=auth.account_id,
            priority=len(self.get_all_teams()) + 1,
            enabled=True,
        )
        team.set_auth_json(auth_json)

        self._db.add(team)
        self._db.commit()

        self._logger.info("team_imported", team_id=team_id, name=team_name, email=auth.email)
        return team

    def switch_to_team(self, team_id: str) -> bool:
        """
        Switch Codex to use a different team's account.

        Writes the team's auth.json to ~/.codex to activate
        that account in Codex.

        Args:
            team_id: Team identifier to switch to.

        Returns:
            True if successful, False otherwise.
        """
        team = self.get_team_by_id(team_id)
        if not team:
            self._logger.warning("team_not_found", team_id=team_id)
            return False

        auth_json = team.get_auth_json()
        if not auth_json:
            self._logger.warning("no_auth_json", team_id=team_id)
            return False

        success = switch_codex_account(auth_json)
        if success:
            self._logger.info("team_switched", team_id=team_id, name=team.name)
        else:
            self._logger.error("team_switch_failed", team_id=team_id)

        return success

    def get_codex_status(self) -> Dict[str, any]:
        """
        Get current Codex login status.

        Returns:
            Dict with login status and account info.
        """
        auth = extract_codex_auth()
        if not auth:
            return {
                "is_logged_in": False,
                "account_id": None,
                "email": None,
                "plan_type": None,
            }

        return {
            "is_logged_in": True,
            "account_id": auth.account_id,
            "email": auth.email,
            "plan_type": auth.plan_type,
        }
