"""Logging utilities for structured logging."""

import logging
import sys
from typing import Any, Dict, Optional
from datetime import datetime

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """
    Configure structured logging for the application.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR).
    """
    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    # Configure structlog
    structlog.configure(
        processors=[
            # Add timestamp to all logs
            structlog.contextvars.merge_contextvars,
            # Add log level
            structlog.processors.add_log_level,
            # Stack trace renderer for errors
            structlog.processors.StackInfoRenderer(),
            # Exception formatter
            structlog.dev.set_exc_info,
            # Timezone-aware timestamp
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # JSON output for production, console for development
            structlog.dev.ConsoleRenderer() if log_level == "DEBUG" else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: Optional[str] = None) -> structlog.BoundLogger:
    """
    Get a structured logger instance.

    Args:
        name: Logger name (usually __name__).

    Returns:
        Configured structlog logger.
    """
    return structlog.get_logger(name)


class SensitiveDataFilter(logging.Filter):
    """
    Filter to redact sensitive data from log records.

    Automatically redacts tokens, keys, and other sensitive
    information from log messages.
    """

    # Patterns to redact
    SENSITIVE_PATTERNS = [
        (r"(sk-[a-zA-Z0-9-]{20,})", "[REDACTED_TOKEN]"),
        (r"(access_token['\"]?\s*[:=]\s*['\"]?)([a-zA-Z0-9-]{20,})", r"\1[REDACTED_TOKEN]"),
        (r"(refresh_token['\"]?\s*[:=]\s*['\"]?)([a-zA-Z0-9-]{20,})", r"\1[REDACTED_TOKEN]"),
        (r"(bearer\s+)([a-zA-Z0-9-]{20,})", r"\1[REDACTED_TOKEN]"),
    ]

    def __init__(self):
        super().__init__()
        # Compile patterns for efficiency
        import re
        self._patterns = [
            (re.compile(pattern, re.IGNORECASE), replacement)
            for pattern, replacement in self.SENSITIVE_PATTERNS
        ]

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Filter and redact sensitive data from log record.

        Args:
            record: Log record to filter.

        Returns:
            True to allow the record.
        """
        if record.msg:
            # Convert to string if needed
            message = str(record.msg)
            for pattern, replacement in self._patterns:
                message = pattern.sub(replacement, message)
            record.msg = message

        return True


def log_team_switch(
    logger: structlog.BoundLogger,
    from_team: str,
    to_team: str,
    reason: str,
    success: bool,
    **kwargs: Any,
) -> None:
    """
    Log a team switch event.

    Args:
        logger: Logger instance.
        from_team: Source team ID.
        to_team: Destination team ID.
        reason: Switch reason (quota_low, manual, error).
        success: Whether the switch succeeded.
        **kwargs: Additional fields to log.
    """
    logger.info(
        event="team_switched",
        from_team=from_team,
        to_team=to_team,
        reason=reason,
        success=success,
        timestamp=datetime.utcnow().isoformat(),
        **kwargs,
    )


def log_usage_check(
    logger: structlog.BoundLogger,
    team_id: str,
    quota_percentage: float,
    threshold: float,
    **kwargs: Any,
) -> None:
    """
    Log a usage check event.

    Args:
        logger: Logger instance.
        team_id: Team ID that was checked.
        quota_percentage: Current quota percentage.
        threshold: Threshold that triggered the check.
        **kwargs: Additional fields to log.
    """
    logger.info(
        event="usage_checked",
        team_id=team_id,
        quota_percentage=quota_percentage,
        threshold=threshold,
        below_threshold=quota_percentage < threshold,
        timestamp=datetime.utcnow().isoformat(),
        **kwargs,
    )
