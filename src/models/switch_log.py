"""Switch log data model for tracking team switches."""

from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, Float, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class SwitchLog(Base):
    """
    Records team switch events for auditing and analysis.

    Tracks the source and destination teams, trigger reason,
    and outcome of each switch operation.
    """

    __tablename__ = "switch_logs"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Foreign key to team
    team_id: Mapped[Optional[str]] = mapped_column(
        String(50), ForeignKey("teams.id"), nullable=True
    )

    # Switch details
    # Timestamp when the switch occurred
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    # Team ID before switch
    from_team_id: Mapped[str] = mapped_column(String(50), nullable=False)
    # Team ID after switch
    to_team_id: Mapped[str] = mapped_column(String(50), nullable=False)
    # Reason for switch: quota_low, manual, error, startup
    reason: Mapped[str] = mapped_column(String(50), nullable=False)
    # Quota percentage of source team when switch was triggered
    from_quota_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Whether the switch was successful
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Error message if switch failed
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Duration of switch in seconds
    switch_duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Relationship back to team
    team = relationship("Team", back_populates="switch_logs")

    def __repr__(self) -> str:
        """Return string representation of the switch log."""
        status = "success" if self.success else "failed"
        return f"<SwitchLog(from={self.from_team_id}, to={self.to_team_id}, {status})>"

    def to_dict(self) -> dict:
        """
        Convert switch log to dictionary.

        Returns:
            Dictionary representation of the switch event.
        """
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "from_team_id": self.from_team_id,
            "to_team_id": self.to_team_id,
            "reason": self.reason,
            "from_quota_percentage": self.from_quota_percentage,
            "success": self.success,
            "error_message": self.error_message,
            "switch_duration_ms": self.switch_duration_ms,
        }


class SwitchReason:
    """Constants for switch trigger reasons."""

    QUOTA_LOW = "quota_low"
    MANUAL = "manual"
    ERROR = "error"
    STARTUP = "startup"
    TOKEN_EXPIRED = "token_expired"
