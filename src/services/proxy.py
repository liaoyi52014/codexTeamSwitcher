"""Proxy Service for seamless token switching."""

import threading
from typing import Callable, Dict, Optional
from flask import Flask, Request, Response, request as flask_request
import requests

from src.utils.logger import get_logger


class TokenProvider:
    """
    Interface for providing tokens to the proxy.

    Implement this to connect with your token management system.
    """

    def get_current_token(self) -> str:
        """Get the current active token."""
        raise NotImplementedError

    def get_team_info(self) -> Dict[str, str]:
        """Get current team information."""
        raise NotImplementedError


class StaticTokenProvider(TokenProvider):
    """Simple token provider with static token."""

    def __init__(self, token: str, team_id: str = "default"):
        self._token = token
        self._team_id = team_id

    def get_current_token(self) -> str:
        return self._token

    def get_team_info(self) -> Dict[str, str]:
        return {"team_id": self._team_id}


class DynamicTokenProvider(TokenProvider):
    """
    Token provider that gets token from a callback function.

    This allows the proxy to automatically use the latest token
    from the team switcher.
    """

    def __init__(self, token_callback: Callable[[], str], team_info_callback: Callable[[], Dict[str, str]]):
        self._get_token = token_callback
        self._get_team_info = team_info_callback

    def get_current_token(self) -> str:
        return self._get_token()

    def get_team_info(self) -> Dict[str, str]:
        return self._get_team_info()


class ProxyService:
    """
    Flask-based proxy for intercepting Codex API requests.

    This proxy sits between the Codex client and the OpenAI API,
    automatically injecting the current team's token. When the
    team switches, the proxy starts using the new token without
    requiring any client changes.
    """

    def __init__(
        self,
        token_provider: TokenProvider,
        host: str = "127.0.0.1",
        port: int = 18888,
        openai_base_url: str = "https://api.openai.com",
    ):
        """
        Initialize the proxy service.

        Args:
            token_provider: Provider for getting current token.
            host: Host to bind to.
            port: Port to listen on.
            openai_base_url: Base URL for OpenAI API.
        """
        self._provider = token_provider
        self._host = host
        self._port = port
        self._base_url = openai_base_url
        self._logger = get_logger(__name__)

        self._app = Flask(__name__)
        self._setup_routes()

        self._server_thread: Optional[threading.Thread] = None
        self._running = False

    def _setup_routes(self) -> None:
        """Set up Flask routes for proxying."""

        @self._app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        def proxy(path: str):
            """Proxy all requests to OpenAI API."""
            return self._handle_proxy(path)

        @self._app.route("/health")
        def health():
            """Health check endpoint."""
            return {"status": "healthy", "team": self._provider.get_team_info()}

        @self._app.route("/team")
        def team_info():
            """Get current team information."""
            return self._provider.get_team_info()

    def _handle_proxy(self, path: str) -> Response:
        """
        Handle proxying a request to OpenAI API.

        Args:
            path: API path to proxy.

        Returns:
            Response from OpenAI API.
        """
        # Get current token from provider
        token = self._provider.get_current_token()
        team_info = self._provider.get_team_info()

        # Build the target URL
        # Handle both /v1/... and direct paths
        if not path.startswith("v1/"):
            path = f"v1/{path}"
        target_url = f"{self._base_url}/{path}"

        # Get request headers
        headers = dict(flask_request.headers)
        # Remove hop-by-hop headers
        hop_headers = {
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
            "transfer-encoding",
            "upgrade",
        }
        for header in hop_headers:
            headers.pop(header, None)

        # Replace Authorization header with current token
        headers["Authorization"] = f"Bearer {token}"

        # Log the request
        self._logger.info(
            "proxy_request",
            method=flask_request.method,
            path=path,
            team_id=team_info.get("team_id", "unknown"),
        )

        # Make the request to OpenAI
        try:
            response = requests.request(
                method=flask_request.method,
                url=target_url,
                headers=headers,
                data=flask_request.get_data(),
                params=flask_request.args,
                cookies=flask_request.cookies,
                timeout=60,
                allow_redirects=False,
            )

            # Return the response
            return Response(
                response.content,
                status=response.status_code,
                headers=dict(response.headers),
            )

        except requests.exceptions.RequestException as e:
            self._logger.error("proxy_error", error=str(e))
            return Response(
                f"Proxy error: {e}",
                status=502,
            )

    def start(self, blocking: bool = True) -> None:
        """
        Start the proxy server.

        Args:
            blocking: If True, runs in blocking mode. If False, runs in background thread.
        """
        if self._running:
            self._logger.warning("proxy_already_running")
            return

        self._running = True

        if blocking:
            self._logger.info(
                "starting_proxy",
                host=self._host,
                port=self._port,
                base_url=self._base_url,
            )
            self._app.run(host=self._host, port=self._port, threaded=True)
        else:
            self._server_thread = threading.Thread(
                target=self._run_server,
                daemon=True,
            )
            self._server_thread.start()
            self._logger.info(
                "proxy_started_background",
                host=self._host,
                port=self._port,
            )

    def _run_server(self) -> None:
        """Run the Flask server in a thread."""
        self._app.run(host=self._host, port=self._port, threaded=True)

    def stop(self) -> None:
        """Stop the proxy server."""
        if not self._running:
            return

        self._running = False
        # Note: Flask's dev server doesn't have a clean stop method
        # In production, use a proper WSGI server (gunicorn, etc.)
        self._logger.info("proxy_stop_requested")

    @property
    def is_running(self) -> bool:
        """Check if the proxy is running."""
        return self._running

    @property
    def url(self) -> str:
        """Get the proxy URL."""
        return f"http://{self._host}:{self._port}"


def create_proxy_from_switcher(
    token_manager,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> ProxyService:
    """
    Create a proxy service from a token manager.

    This is a convenience function to connect the proxy with
    the token management system.

    Args:
        token_manager: TokenManager instance.
        host: Proxy host.
        port: Proxy port.

    Returns:
        Configured ProxyService instance.
    """
    # Create callbacks for dynamic token provider
    current_team_id = [None]

    def get_token():
        team_id = current_team_id[0] or token_manager.get_active_team()
        if team_id:
            current_team_id[0] = team_id.id
            return token_manager.get_decrypted_token(team_id.id)
        return ""

    def get_team_info():
        team_id = current_team_id[0] or token_manager.get_active_team()
        if team_id:
            current_team_id[0] = team_id.id
            team = token_manager.get_team_by_id(team_id.id)
            if team:
                return {
                    "team_id": team.id,
                    "team_name": team.name,
                    "quota_percentage": f"{team.quota_percentage:.1f}" if team.quota_percentage else "unknown",
                }
        return {"team_id": "unknown", "team_name": "unknown"}

    provider = DynamicTokenProvider(get_token, get_team_info)
    return ProxyService(provider, host=host, port=port)
