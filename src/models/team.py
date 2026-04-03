"""Team data model for managing Codex teams."""

import json
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import String, Integer, Float, Boolean, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.models.base import Base


class Team(Base):
    """
    Represents a Codex team with OAuth credentials and quota tracking.

    This model stores team configuration including OAuth tokens,
    current quota usage, and operational status.
    """

    # Primary identifier for the team
    __tablename__ = "teams"

    # Column definitions
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # OAuth credentials (stored encrypted)
    # Access token for API authentication
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    # Refresh token for obtaining new access tokens
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Token expiration timestamp
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Organization ID from OAuth
    organization_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Full auth JSON for account switching (stored as JSON string, encrypted)
    # This stores the complete auth.json content needed to switch accounts
    auth_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Quota tracking fields
    # Total quota allocated to the team
    quota_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=0)
    # Amount of quota already used
    quota_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=0)
    # Remaining quota available
    quota_remaining: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=0)
    # Percentage of quota remaining (5-hour window)
    quota_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=100.0)
    # 5-hour window usage percentage (remaining)
    quota_5h_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=100.0)
    # Weekly usage percentage (remaining)
    quota_weekly_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=100.0)
    # Next refresh time for 5-hour window
    quota_5h_refresh_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Next refresh time for weekly window
    quota_weekly_refresh_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Last time quota was checked
    quota_last_checked: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Team configuration
    # Priority for team selection (lower number = higher priority)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Whether this team is currently active
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Custom status command (defaults to /status)
    status_command: Mapped[str] = mapped_column(String(50), nullable=False, default="/status")

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationship to switch logs
    switch_logs = relationship("SwitchLog", back_populates="team", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        """Return string representation of the team."""
        return f"<Team(id={self.id}, name={self.name}, quota_pct={self.quota_percentage:.1f}%)>"

    @property
    def is_token_expired(self) -> bool:
        """Check if the access token has expired."""
        if self.expires_at is None:
            # No expiration set, assume valid
            return False
        return datetime.utcnow() >= self.expires_at

    @property
    def is_quota_low(self) -> bool:
        """Check if quota is below the default threshold (5%)."""
        return self.quota_percentage is not None and self.quota_percentage < 5.0

    def check_quota_low(self, threshold: float = 5.0) -> bool:
        """Check if quota is below a specific threshold percentage."""
        return self.quota_percentage is not None and self.quota_percentage < threshold

    def to_dict(self, include_auth: bool = False) -> dict:
        """
        Convert team to dictionary (excluding sensitive token data).

        Args:
            include_auth: If True, includes the auth_json data.

        Returns:
            Dictionary representation without full tokens.
        """
        # Get subscription info from auth_json
        subscription = self.get_subscription_info()

        # Extract organization/workspace name from auth_json
        organization_name = None
        auth_data = self.get_auth_json()
        if auth_data:
            try:
                from src.utils.codex_auth import extract_codex_auth
                auth_info = extract_codex_auth(auth_json=auth_data)
                if auth_info:
                    organization_name = auth_info.organization_name
            except Exception:
                pass

        result = {
            "id": self.id,
            "name": self.name,
            "organization_id": self.organization_id,
            "organization_name": organization_name,
            "priority": self.priority,
            "enabled": self.enabled,
            "status_command": self.status_command,
            "quota": {
                "total": self.quota_total,
                "used": self.quota_used,
                "remaining": self.quota_remaining,
                "percentage": self.quota_percentage,
                "percentage_5h": self.quota_5h_percentage,
                "percentage_weekly": self.quota_weekly_percentage,
                "refresh_at_5h": self.quota_5h_refresh_at.isoformat() if self.quota_5h_refresh_at else None,
                "refresh_at_weekly": self.quota_weekly_refresh_at.isoformat() if self.quota_weekly_refresh_at else None,
                "last_checked": self.quota_last_checked.isoformat() if self.quota_last_checked else None,
            },
            "subscription": subscription,
            "is_token_expired": self.is_token_expired,
            "is_quota_low": self.is_quota_low,
        }

        if include_auth and self.auth_json:
            try:
                result["auth_json"] = json.loads(self.auth_json)
            except json.JSONDecodeError:
                pass

        return result

    def get_auth_json(self) -> Optional[Dict[str, Any]]:
        """
        Get the stored auth_json as a dictionary.

        Returns:
            Auth JSON dict, or None if not set.
        """
        if not self.auth_json:
            return None
        try:
            return json.loads(self.auth_json)
        except json.JSONDecodeError:
            return None

    def set_auth_json(self, auth_data: Dict[str, Any]) -> None:
        """
        Set the auth_json from a dictionary.

        Args:
            auth_data: Auth JSON dict to store.
        """
        self.auth_json = json.dumps(auth_data)

    def get_subscription_info(self) -> Optional[Dict[str, str]]:
        """
        Extract subscription info from stored auth_json.

        Returns:
            Dict with subscription_active_start, subscription_active_until, plan_type.
        """
        auth_json = self.get_auth_json()
        if not auth_json:
            return None

        try:
            from src.utils.codex_auth import extract_codex_auth
            auth = extract_codex_auth(auth_json=auth_json)
            if auth:
                return {
                    "plan_type": auth.plan_type,
                    "subscription_active_start": auth.subscription_active_start,
                    "subscription_active_until": auth.subscription_active_until,
                }
        except Exception:
            pass

        return None
