"""Main entry point for Codex Team Switcher."""

import os
import sys
import signal
import argparse
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.config import load_config, ensure_data_directory
from src.models.base import Base
from src.models.team import Team
from src.models.switch_log import SwitchLog
from src.services import (
    TokenManager,
    UsageMonitor,
    TeamSwitcher,
    ProxyService,
    AdminInterface,
    SwitchReason,
)
from src.utils import (
    configure_logging,
    get_logger,
    generate_encryption_key,
)


class CodexTeamSwitcher:
    """
    Main application class for Codex Team Switcher.

    Orchestrates all services: token management, usage monitoring,
    team switching, and proxy server.
    """

    def __init__(self, config_path: str = None):
        """
        Initialize the application.

        Args:
            config_path: Path to configuration file.
        """
        # Load configuration
        self._config = load_config(config_path)

        # Configure logging
        configure_logging(self._config.app.log_level)
        self._logger = get_logger(__name__)

        # Ensure data directory exists
        ensure_data_directory(self._config)

        # Initialize database
        self._engine = None
        self._Session = None
        self._db_session = None

        # Initialize services
        self._token_manager = None
        self._usage_monitor = None
        self._team_switcher = None
        self._proxy_service = None
        self._admin_interface = None

        # Stop event for graceful shutdown
        self._stop_event = None

    def initialize(self) -> None:
        """Initialize database and services."""
        self._logger.info("initializing_application")

        # Create database engine
        db_path = os.path.abspath(self._config.database.db_path)
        self._engine = create_engine(f"sqlite:///{db_path}")

        # Create tables
        Base.metadata.create_all(self._engine)

        # Create session
        self._Session = sessionmaker(bind=self._engine)
        self._db_session = self._Session()

        # Get or generate encryption key
        encryption_key = self._config.security.encryption_key
        if not encryption_key:
            # Try to load from file
            from src.utils.crypto import load_encryption_key, save_encryption_key
            encryption_key = load_encryption_key()

        if not encryption_key:
            # Generate new key and save it
            encryption_key = generate_encryption_key()
            save_encryption_key(encryption_key)
            self._logger.warning(
                "encryption_key_generated",
                key=encryption_key,
                message="Key saved to data/.encryption_key",
            )

        # Initialize Token Manager
        self._token_manager = TokenManager(self._db_session, encryption_key)

        # Initialize Usage Monitor
        self._usage_monitor = UsageMonitor(
            token_manager=self._token_manager,
            threshold_percentage=self._config.monitor.threshold_percentage,
            check_interval_seconds=self._config.monitor.check_interval_seconds,
        )

        # Initialize Team Switcher
        self._team_switcher = TeamSwitcher(
            token_manager=self._token_manager,
            db_session=self._db_session,
            threshold_percentage=self._config.monitor.threshold_percentage,
        )

        # Set up callbacks
        self._setup_callbacks()

        # Sync teams from config to database
        self._sync_teams_from_config()

        # Auto-import current Codex account if no teams exist
        teams = self._token_manager.get_all_teams()
        if len(teams) == 0:
            self._logger.info("no_teams_found_attempting_import")
            imported = self._token_manager.import_current_codex_account()
            if imported:
                self._logger.info("auto_imported_team", team_id=imported.id, name=imported.name)
            else:
                self._logger.warning("auto_import_failed_no_codex_login")

        # Set current team
        active_team = self._token_manager.get_active_team()
        if active_team:
            self._team_switcher.set_current_team(active_team.id)
            self._logger.info("active_team_set", team_id=active_team.id)

        self._logger.info("application_initialized")

    def _setup_callbacks(self) -> None:
        """Set up service callbacks."""

        # When quota is low, trigger team switch
        def on_quota_low(team: Team, usage_info):
            self._logger.warning(
                "quota_low_detected",
                team_id=team.id,
                team_name=team.name,
                usage_percentage=usage_info.percentage,
            )

            if self._config.monitor.auto_switch_enabled:
                self._handle_auto_switch(team, usage_info)

        # When all teams checked, broadcast to WebSocket clients
        def on_all_teams_checked(results):
            # Push usage update to WebSocket clients
            if self._admin_interface and hasattr(self._admin_interface, "broadcast_usage_update"):
                status = self.get_status()
                self._admin_interface.broadcast_usage_update(status)

        self._usage_monitor.set_quota_low_callback(on_quota_low)
        self._usage_monitor.set_all_teams_checked_callback(on_all_teams_checked)

    def _handle_auto_switch(self, team: Team, usage_info) -> None:
        """
        Handle automatic team switch when quota is low.

        Args:
            team: Current team with low quota.
            usage_info: Usage information for the team.
        """
        self._logger.info("attempting_auto_switch", team_id=team.id)

        try:
            success = self._team_switcher.switch_to_next_team(
                reason=SwitchReason.QUOTA_LOW
            )
            if success:
                new_team = self._team_switcher.get_current_team()
                self._logger.info(
                    "auto_switch_success",
                    from_team=team.id,
                    to_team=new_team.id if new_team else "unknown",
                )
            else:
                self._logger.error("auto_switch_failed", reason="no_available_team")

        except Exception as e:
            self._logger.error("auto_switch_error", error=str(e))

    def _sync_teams_from_config(self) -> None:
        """Synchronize teams from config file to database."""
        from src.utils.codex_auth import load_codex_token

        existing_teams = {t.id: t for t in self._token_manager.get_all_teams()}
        synced_count = 0
        skipped_count = 0

        for team_config in self._config.teams:
            # Get access token - either from config or from Codex auth
            access_token = team_config.access_token
            if team_config.use_codex_auth:
                codex_token = load_codex_token()
                if codex_token:
                    access_token = codex_token
                    self._logger.info("using_codex_auth_token", team_id=team_config.id)
                else:
                    self._logger.warning("codex_auth_requested_but_not_available", team_id=team_config.id)

            # Backward compatibility: skip placeholder teams from old templates.
            if not team_config.use_codex_auth and access_token == "sk-your-token-here":
                self._logger.warning(
                    "skipping_placeholder_team_from_config",
                    team_id=team_config.id,
                    team_name=team_config.name,
                )
                skipped_count += 1
                continue

            if team_config.id in existing_teams:
                # Update existing team
                team = existing_teams[team_config.id]
                team.name = team_config.name
                team.enabled = team_config.enabled
                team.priority = team_config.priority
                team.status_command = team_config.status_command

                if access_token:
                    self._token_manager.update_team_token(
                        team_id=team.id,
                        access_token=access_token,
                        refresh_token=team_config.refresh_token or None,
                    )
                synced_count += 1
            else:
                # Add new team
                from datetime import datetime

                expires_at = None
                if team_config.expires_at:
                    try:
                        expires_at = datetime.fromisoformat(team_config.expires_at.replace("Z", "+00:00"))
                    except ValueError:
                        pass

                self._token_manager.add_team(
                    team_id=team_config.id,
                    name=team_config.name,
                    access_token=access_token,
                    refresh_token=team_config.refresh_token or None,
                    expires_at=expires_at,
                    organization_id=team_config.organization_id or None,
                    priority=team_config.priority,
                    status_command=team_config.status_command,
                )
                synced_count += 1

        self._db_session.commit()
        self._logger.info(
            "teams_synced",
            count=synced_count,
            skipped=skipped_count,
            configured=len(self._config.teams),
        )

    def start_proxy(self, blocking: bool = True) -> None:
        """
        Start the proxy server.

        Args:
            blocking: Whether to run in blocking mode.
        """
        from src.services.proxy import create_proxy_from_switcher

        self._proxy_service = create_proxy_from_switcher(
            token_manager=self._token_manager,
            host=self._config.app.proxy_host,
            port=self._config.app.proxy_port,
        )

        self._logger.info(
            "starting_proxy",
            host=self._config.app.proxy_host,
            port=self._config.app.proxy_port,
        )

        self._proxy_service.start(blocking=blocking)

    def start_admin(self, blocking: bool = False) -> None:
        """
        Start the web admin interface.

        Args:
            blocking: Whether to run in blocking mode.
        """
        self._admin_interface = AdminInterface(
            app_handler=self,
            host="0.0.0.0",
            port=18080,
        )

        self._logger.info(
            "starting_admin_interface",
            port=18080,
            url="http://localhost:18080",
        )

        self._admin_interface.start(blocking=blocking)

    def start_monitoring(self) -> None:
        """Start usage monitoring loop."""
        self._logger.info("starting_monitoring")
        self._usage_monitor.start_monitoring(stop_event=self._stop_event)

    def run_single_check(self) -> None:
        """Run a single usage check and exit."""
        self._logger.info("running_single_check")
        result = self._usage_monitor.run_single_check()

        if result.success:
            self._logger.info(
                "check_complete",
                team_id=result.team_id,
                percentage=result.usage.percentage if result.usage else 0,
            )
        else:
            self._logger.error("check_failed", error=result.error)

    def get_status(self) -> dict:
        """
        Get current system status.

        Returns:
            Dictionary with status information.
        """
        current_team = self._team_switcher.get_current_team()
        teams_status = self._token_manager.get_teams_by_status()

        return {
            "current_team": current_team.to_dict() if current_team else None,
            "teams": {
                "active": [t.to_dict() for t in teams_status["active"]],
                "quota_low": [t.to_dict() for t in teams_status["quota_low"]],
                "expired": [t.to_dict() for t in teams_status["expired"]],
                "disabled": [t.to_dict() for t in teams_status["disabled"]],
            },
            "switch_history": [
                log.to_dict()
                for log in self._team_switcher.get_switch_history(limit=5)
            ],
        }

    def shutdown(self) -> None:
        """Clean up resources and shutdown."""
        self._logger.info("shutting_down")

        if self._proxy_service:
            self._proxy_service.stop()

        if self._db_session:
            self._db_session.close()

        if self._engine:
            self._engine.dispose()

        self._logger.info("shutdown_complete")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Codex Team Switcher")
    parser.add_argument(
        "--config",
        "-c",
        help="Path to configuration file",
        default=None,
    )
    parser.add_argument(
        "--proxy-only",
        action="store_true",
        help="Start only the proxy server",
    )
    parser.add_argument(
        "--admin-only",
        action="store_true",
        help="Start only the admin interface",
    )
    parser.add_argument(
        "--no-admin",
        action="store_true",
        help="Disable admin web interface",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run a single usage check and exit",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current status and exit",
    )

    args = parser.parse_args()

    # Create application
    app = CodexTeamSwitcher(config_path=args.config)

    # Set up signal handlers
    def signal_handler(signum, frame):
        app.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Initialize
    app.initialize()

    # Handle commands
    if args.status:
        import json
        print(json.dumps(app.get_status(), indent=2, default=str))
        app.shutdown()
        return

    if args.check:
        app.run_single_check()
        app.shutdown()
        return

    if args.proxy_only:
        app.start_proxy(blocking=True)
    elif args.admin_only:
        app.start_admin(blocking=True)
    else:
        # Start proxy in background
        app.start_proxy(blocking=False)

        # Start admin interface in background (unless --no-admin is set)
        if not args.no_admin:
            app.start_admin(blocking=False)

        # Start monitoring
        app.start_monitoring()


if __name__ == "__main__":
    main()
