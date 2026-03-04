"""Settings and configuration management."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import yaml


@dataclass
class TeamConfig:
    """Team configuration from YAML."""

    id: str
    name: str
    enabled: bool = True
    priority: int = 1
    access_token: str = ""
    refresh_token: str = ""
    expires_at: str = ""
    organization_id: str = ""
    status_command: str = "/status"
    # If true, load token from ~/.codex/auth.json
    use_codex_auth: bool = False


@dataclass
class AppConfig:
    """Application configuration."""

    proxy_host: str = "127.0.0.1"
    proxy_port: int = 18888
    log_level: str = "INFO"


@dataclass
class DatabaseConfig:
    """Database configuration."""

    db_path: str = "./data/teams.db"


@dataclass
class MonitorConfig:
    """Monitor service configuration."""

    threshold_percentage: float = 5.0
    check_interval_seconds: int = 300
    auto_switch_enabled: bool = True
    retry_attempts: int = 3
    retry_delay_seconds: int = 5


@dataclass
class SecurityConfig:
    """Security configuration."""

    encryption_key: str = ""
    env_prefix: str = "CODEX_"


@dataclass
class Settings:
    """Main application settings."""

    app: AppConfig = field(default_factory=AppConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    teams: List[TeamConfig] = field(default_factory=list)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)


def load_config(config_path: Optional[str] = None) -> Settings:
    """
    Load configuration from YAML file.

    Args:
        config_path: Path to config file. If None, looks for config.yaml.

    Returns:
        Settings object with loaded configuration.

    Raises:
        FileNotFoundError: If config file not found.
        ValueError: If config is invalid.
    """
    if config_path is None:
        # Default paths to try
        possible_paths = [
            "config.yaml",
            "config.local.yaml",
            os.path.join(os.path.dirname(__file__), "..", "..", "config.yaml"),
        ]
        for path in possible_paths:
            if os.path.exists(path):
                config_path = path
                break

    if config_path is None or not os.path.exists(config_path):
        # Return default settings if no config file found
        return Settings()

    # Load YAML
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)

    if not data:
        return Settings()

    # Parse configuration
    settings = Settings()

    # App config
    if "app" in data:
        app_data = data["app"]
        settings.app = AppConfig(
            proxy_host=app_data.get("proxy_host", "127.0.0.1"),
            proxy_port=app_data.get("proxy_port", 8080),
            log_level=app_data.get("log_level", "INFO"),
        )

    # Database config
    if "database" in data:
        db_data = data["database"]
        settings.database = DatabaseConfig(
            db_path=db_data.get("db_path", "./data/teams.db"),
        )

    # Monitor config
    if "monitor" in data:
        mon_data = data["monitor"]
        settings.monitor = MonitorConfig(
            threshold_percentage=mon_data.get("threshold_percentage", 5.0),
            check_interval_seconds=mon_data.get("check_interval_seconds", 300),
            auto_switch_enabled=mon_data.get("auto_switch_enabled", True),
            retry_attempts=mon_data.get("retry_attempts", 3),
            retry_delay_seconds=mon_data.get("retry_delay_seconds", 5),
        )

    # Security config
    if "security" in data:
        sec_data = data["security"]
        settings.security = SecurityConfig(
            encryption_key=sec_data.get("encryption_key", ""),
            env_prefix=sec_data.get("env_prefix", "CODEX_"),
        )

    # Team configs
    teams = []
    if "teams" in data:
        for team_data in data["teams"]:
            oauth = team_data.get("oauth", {})

            team = TeamConfig(
                id=team_data["id"],
                name=team_data.get("name", team_data["id"]),
                enabled=team_data.get("enabled", True),
                priority=team_data.get("priority", 1),
                access_token=oauth.get("access_token", ""),
                refresh_token=oauth.get("refresh_token", ""),
                expires_at=oauth.get("expires_at", ""),
                organization_id=oauth.get("organization_id", ""),
                status_command=team_data.get("status_command", "/status"),
                use_codex_auth=team_data.get("use_codex_auth", False),
            )
            teams.append(team)

    settings.teams = teams

    # Override with environment variables
    _apply_env_overrides(settings)

    return settings


def _apply_env_overrides(settings: Settings) -> None:
    """
    Apply environment variable overrides to settings.

    Environment variables take precedence over config file values.
    """
    prefix = settings.security.env_prefix

    # Check for encryption key
    env_key = os.environ.get(f"{prefix}ENCRYPTION_KEY")
    if env_key:
        settings.security.encryption_key = env_key

    # Check for log level
    env_log_level = os.environ.get(f"{prefix}LOG_LEVEL")
    if env_log_level:
        settings.app.log_level = env_log_level

    # Check for proxy port
    env_port = os.environ.get(f"{prefix}PROXY_PORT")
    if env_port:
        try:
            settings.app.proxy_port = int(env_port)
        except ValueError:
            pass

    # Check for threshold
    env_threshold = os.environ.get(f"{prefix}THRESHOLD")
    if env_threshold:
        try:
            settings.monitor.threshold_percentage = float(env_threshold)
        except ValueError:
            pass


def ensure_data_directory(settings: Settings) -> None:
    """
    Ensure the data directory exists.

    Args:
        settings: Application settings.
    """
    db_path = Path(settings.database.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
