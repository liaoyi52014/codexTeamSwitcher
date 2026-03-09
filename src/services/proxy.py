"""Proxy Service for seamless token switching."""

import threading
from typing import Any, Callable, Dict, Optional

import requests
from flask import Flask, Response, request as flask_request

from src.utils.logger import get_logger


class TokenProvider:
    """
    Interface for providing tokens to the proxy.

    Implement this to connect with your token management system.
    """

    def get_current_token(self) -> str:
        """Get the current active token."""
        raise NotImplementedError

    def get_team_info(self) -> Dict[str, Any]:
        """Get current team information."""
        raise NotImplementedError


class StaticTokenProvider(TokenProvider):
    """Simple token provider with static token."""

    def __init__(self, token: str, team_id: str = "default"):
        self._token = token
        self._team_id = team_id

    def get_current_token(self) -> str:
        return self._token

    def get_team_info(self) -> Dict[str, Any]:
        return {"team_id": self._team_id}


class DynamicTokenProvider(TokenProvider):
    """
    Token provider that gets token from a callback function.

    This allows the proxy to automatically use the latest token
    from the team switcher.
    """

    def __init__(
        self,
        token_callback: Callable[[], str],
        team_info_callback: Callable[[], Dict[str, Any]],
    ):
        self._get_token = token_callback
        self._get_team_info = team_info_callback

    def get_current_token(self) -> str:
        return self._get_token()

    def get_team_info(self) -> Dict[str, Any]:
        return self._get_team_info()


class ProxyService:
    """
    Flask-based proxy for intercepting Codex API requests.

    This proxy sits between the Codex client and the OpenAI API,
    automatically injecting the current team's token. When the
    team switches, the proxy starts using the new token without
    requiring any client changes.
    """

    _CHATGPT_PATH_PREFIXES = (
        "backend-api/",
        "api/codex/",
        "api/wham/",
        "api/auth/",
    )

    def __init__(
        self,
        token_provider: TokenProvider,
        host: str = "127.0.0.1",
        port: int = 18888,
        openai_base_url: str = "https://api.openai.com",
        chatgpt_base_url: str = "https://chatgpt.com",
        allow_auth_fallback: bool = True,
    ):
        """
        Initialize the proxy service.

        Args:
            token_provider: Provider for getting current token.
            host: Host to bind to.
            port: Port to listen on.
            openai_base_url: Base URL for OpenAI API.
            chatgpt_base_url: Base URL for ChatGPT backend APIs.
            allow_auth_fallback: Retry with original caller auth when managed
                team auth is rejected. Enabled by default for non-invasive behavior.
        """
        self._provider = token_provider
        self._host = host
        self._port = port
        self._base_url = openai_base_url
        self._chatgpt_base_url = chatgpt_base_url.rstrip("/")
        self._allow_auth_fallback = allow_auth_fallback
        self._logger = get_logger(__name__)

        self._app = Flask(__name__)
        self._setup_routes()

        self._server_thread: Optional[threading.Thread] = None
        self._running = False

    def _setup_routes(self) -> None:
        """Set up Flask routes for proxying."""

        @self._app.route("/")
        def index():
            """Root endpoint with proxy status and quick links."""
            return {
                "service": "codex-team-switcher-proxy",
                "status": "healthy",
                "team": self._provider.get_team_info(),
                "endpoints": {
                    "health": "/health",
                    "team": "/team",
                    "proxy_example": "/v1/models",
                },
            }

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
        original_authorization = flask_request.headers.get("Authorization")

        normalized_path = self._normalize_target_path(path)
        target_base_url = self._select_target_base_url(normalized_path)
        target_url = f"{target_base_url}/{normalized_path}"

        # Filter incoming headers case-insensitively.
        # In practice Flask exposes keys like "Host"/"Connection", so a
        # lowercase `dict.pop("host")` will not remove them.
        excluded_headers = {
            "host",
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
            "transfer-encoding",
            "upgrade",
        }
        base_headers = {
            key: value
            for key, value in flask_request.headers.items()
            if key.lower() not in excluded_headers
        }
        headers = dict(base_headers)

        # Prefer managed team token; if unavailable, preserve caller auth to
        # keep proxy transparent for normal Codex usage.
        managed_authorization = f"Bearer {token}" if token else None
        if managed_authorization:
            headers["Authorization"] = managed_authorization
        elif original_authorization:
            headers["Authorization"] = original_authorization

        managed_account_id = team_info.get("account_id")
        if managed_account_id:
            headers["ChatGPT-Account-Id"] = managed_account_id

        # Log the request
        self._logger.info(
            "proxy_request",
            method=flask_request.method,
            path=normalized_path,
            upstream=target_base_url,
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

            # Keep proxy non-invasive by default: retry with caller auth when
            # managed credentials are rejected.
            if (
                self._allow_auth_fallback
                and response.status_code in (401, 403)
                and managed_authorization
                and original_authorization
                and original_authorization != managed_authorization
            ):
                fallback_headers = dict(base_headers)
                fallback_headers["Authorization"] = original_authorization
                fallback_response = requests.request(
                    method=flask_request.method,
                    url=target_url,
                    headers=fallback_headers,
                    data=flask_request.get_data(),
                    params=flask_request.args,
                    cookies=flask_request.cookies,
                    timeout=60,
                    allow_redirects=False,
                )

                if fallback_response.status_code < response.status_code:
                    self._logger.info(
                        "proxy_auth_fallback_used",
                        path=normalized_path,
                        original_status=response.status_code,
                        fallback_status=fallback_response.status_code,
                        team_id=team_info.get("team_id", "unknown"),
                    )
                    response = fallback_response
                else:
                    self._logger.warning(
                        "proxy_auth_rejected",
                        path=normalized_path,
                        status=response.status_code,
                        team_id=team_info.get("team_id", "unknown"),
                    )
            elif (
                response.status_code in (401, 403)
                and managed_authorization
                and original_authorization
                and original_authorization != managed_authorization
            ):
                self._logger.warning(
                    "proxy_auth_rejected_no_fallback",
                    path=normalized_path,
                    status=response.status_code,
                    team_id=team_info.get("team_id", "unknown"),
                )

            # Remove hop-by-hop response headers before returning to client.
            response_headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in excluded_headers
            }

            # Return the response
            return Response(
                response.content,
                status=response.status_code,
                headers=response_headers,
            )

        except requests.exceptions.RequestException as e:
            self._logger.error("proxy_error", error=str(e))
            return Response(
                f"Proxy error: {e}",
                status=502,
            )

    def _normalize_target_path(self, path: str) -> str:
        """
        Normalize incoming path for upstream APIs.

        Keeps known non-v1 paths (e.g. ChatGPT backend APIs) intact.
        """
        normalized = path.lstrip("/")
        if not normalized:
            return "v1/models"

        if normalized.startswith("v1/") or normalized.startswith(
            self._CHATGPT_PATH_PREFIXES
        ):
            return normalized

        return f"v1/{normalized}"

    def _select_target_base_url(self, normalized_path: str) -> str:
        """Select upstream base URL by path family."""
        if normalized_path.startswith(self._CHATGPT_PATH_PREFIXES):
            return self._chatgpt_base_url
        return self._base_url

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
    team_switcher=None,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> ProxyService:
    """
    Create a proxy service from a token manager.

    This is a convenience function to connect the proxy with
    the token management system.

    Args:
        token_manager: TokenManager instance.
        team_switcher: Optional TeamSwitcher for runtime active-team selection.
        host: Proxy host.
        port: Proxy port.

    Returns:
        Configured ProxyService instance.
    """
    from src.utils.codex_auth import extract_codex_auth

    logger = get_logger(__name__)

    def get_current_team():
        """Resolve active team with explicit runtime preference."""
        if team_switcher:
            try:
                runtime_team = team_switcher.get_current_team()
                if runtime_team:
                    team = token_manager.get_team_by_id(runtime_team.id)
                    if team:
                        return team
            except Exception as e:
                logger.warning("proxy_runtime_team_lookup_failed", error=str(e))

        matched_team = token_manager.get_team_matching_codex_auth()
        if matched_team:
            return matched_team

        return token_manager.get_active_team()

    def get_token():
        team = get_current_team()
        if team:
            try:
                return token_manager.get_decrypted_token(team.id)
            except Exception as e:
                logger.error("proxy_team_token_unavailable", team_id=team.id, error=str(e))
        return ""

    def get_team_info():
        team = get_current_team()
        if team:
            account_id = None
            auth_json = team.get_auth_json()
            if auth_json:
                auth = extract_codex_auth(auth_json=auth_json)
                if auth:
                    account_id = auth.account_id

            return {
                "team_id": team.id,
                "team_name": team.name,
                "quota_percentage": (
                    f"{team.quota_percentage:.1f}"
                    if team.quota_percentage is not None
                    else "unknown"
                ),
                "account_id": account_id,
            }
        return {"team_id": "unknown", "team_name": "unknown"}

    provider = DynamicTokenProvider(get_token, get_team_info)
    return ProxyService(provider, host=host, port=port)
