"""Codex authentication utilities."""

import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass


@dataclass
class CodexAuth:
    """Extracted Codex authentication information."""
    account_id: str
    access_token: str
    email: Optional[str] = None
    plan_type: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_at: Optional[str] = None
    organization_id: Optional[str] = None
    organization_name: Optional[str] = None
    # Subscription info from JWT auth claim
    subscription_active_start: Optional[str] = None
    subscription_active_until: Optional[str] = None
    subscription_last_checked: Optional[str] = None


def _select_default_organization(organizations: Any) -> Optional[Dict[str, Any]]:
    """
    Select the default organization/workspace from a list.

    Prefers item with `is_default=true`, falls back to first entry.
    """
    if not isinstance(organizations, list) or not organizations:
        return None

    valid_orgs: List[Dict[str, Any]] = [
        org for org in organizations if isinstance(org, dict)
    ]
    if not valid_orgs:
        return None

    for org in valid_orgs:
        if org.get("is_default"):
            return org

    return valid_orgs[0]


def get_codex_auth_path() -> Path:
    """Get the path to Codex auth file."""
    return Path.home() / ".codex" / "auth.json"


def load_codex_auth_json() -> Optional[Dict[str, Any]]:
    """
    Load the full Codex auth.json file.

    Returns:
        Auth JSON as dict, or None if not found.
    """
    auth_path = get_codex_auth_path()
    if not auth_path.exists():
        return None

    try:
        with open(auth_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def extract_codex_auth(auth_json: Optional[Dict[str, Any]] = None) -> Optional[CodexAuth]:
    """
    Extract authentication details from Codex auth.json.

    Parses the auth.json to extract account_id, access_token,
    email, and plan_type.

    Args:
        auth_json: Auth JSON dict. If None, loads from file.

    Returns:
        CodexAuth object with extracted information, or None if extraction fails.
    """
    if auth_json is None:
        auth_json = load_codex_auth_json()
        if auth_json is None:
            return None

    try:
        # Get tokens object
        mode = str(auth_json.get("auth_mode", "")).lower()
        tokens = auth_json.get("tokens")
        if not isinstance(tokens, dict):
            if mode and mode not in ("chatgpt", "chatgpt_auth_tokens"):
                return None
            return None

        # Get access_token
        access_token = tokens.get("access_token")
        if not access_token:
            return None

        # Get email and plan_type from JWT
        account_id = tokens.get("account_id")
        email = None
        plan_type = None
        organization_id = None
        organization_name = None
        # Subscription info
        subscription_active_start = None
        subscription_active_until = None
        subscription_last_checked = None

        id_token = tokens.get("id_token")
        if id_token:
            claims = _decode_jwt_payload(id_token)
            if claims:
                email = claims.get("email")
                auth_claim = claims.get("https://api.openai.com/auth", {})
                if not account_id:
                    account_id = auth_claim.get("chatgpt_account_id")
                plan_type = auth_claim.get("chatgpt_plan_type")

                # Extract subscription info from auth claim
                subscription_active_start = auth_claim.get("chatgpt_subscription_active_start")
                subscription_active_until = auth_claim.get("chatgpt_subscription_active_until")
                subscription_last_checked = auth_claim.get("chatgpt_subscription_last_checked")

                # Most id_tokens place organizations under the auth claim.
                organizations = auth_claim.get("organizations")
                if organizations is None:
                    # Backward compatibility: some payloads may expose it at top-level.
                    organizations = claims.get("organizations")

                org = _select_default_organization(organizations)
                if org:
                    organization_id = org.get("id")
                    organization_name = org.get("title") or org.get("name")

        if not account_id:
            # Final fallback: try extracting from JWT helper.
            if id_token:
                account_id = _extract_account_id_from_jwt(id_token)
            if not account_id:
                return None

        return CodexAuth(
            account_id=account_id,
            access_token=access_token,
            email=email,
            plan_type=plan_type,
            refresh_token=tokens.get("refresh_token"),
            expires_at=tokens.get("expires_at"),
            organization_id=organization_id,
            organization_name=organization_name,
            subscription_active_start=subscription_active_start,
            subscription_active_until=subscription_active_until,
            subscription_last_checked=subscription_last_checked,
        )

    except Exception:
        return None


def _extract_account_id_from_jwt(id_token: str) -> Optional[str]:
    """Extract account_id from JWT token."""
    claims = _decode_jwt_payload(id_token)
    if not claims:
        return None

    # Try auth claim first
    auth_claim = claims.get("https://api.openai.com/auth", {})
    if auth_claim:
        return auth_claim.get("chatgpt_account_id")

    return None


def _decode_jwt_payload(token: str) -> Optional[Dict[str, Any]]:
    """Decode JWT payload (without verification)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        # Decode base64url payload
        import base64
        payload_b64 = parts[1]
        # Add padding if needed
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding

        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        return json.loads(payload_bytes)
    except Exception:
        return None


def load_codex_token() -> Optional[str]:
    """
    Load access token from Codex auth file.

    Returns:
        Access token string if available, None otherwise.
    """
    auth = extract_codex_auth()
    if auth:
        return auth.access_token

    # Fallback: try direct load
    auth_json = load_codex_auth_json()
    if not auth_json:
        return None

    tokens = auth_json.get("tokens", {})
    access_token = tokens.get("access_token")
    if access_token:
        return access_token

    # Fallback to OPENAI_API_KEY
    return auth_json.get("OPENAI_API_KEY")


def get_codex_account_id(auth_json: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """
    Get account ID from Codex auth file.

    Args:
        auth_json: Optional auth JSON object. If omitted, loads ~/.codex/auth.json.

    Returns:
        Account ID string if available, None otherwise.
    """
    auth = extract_codex_auth(auth_json=auth_json)
    if auth:
        return auth.account_id

    if auth_json is None:
        auth_json = load_codex_auth_json()
    if not auth_json:
        return None

    tokens = auth_json.get("tokens", {})
    return tokens.get("account_id")


def is_codex_logged_in() -> bool:
    """Check if Codex is logged in."""
    return load_codex_token() is not None


def switch_codex_account(auth_json: Dict[str, Any]) -> bool:
    """
    Switch Codex to use a different account by writing auth.json.

    This allows switching between different Codex teams/accounts.

    Args:
        auth_json: The auth.json content to write.

    Returns:
        True if successful, False otherwise.
    """
    try:
        auth_path = get_codex_auth_path()
        # Ensure directory exists
        auth_path.parent.mkdir(parents=True, exist_ok=True)

        # Write the auth.json file
        with open(auth_path, "w") as f:
            json.dump(auth_json, f, indent=2)

        # Terminate all active Codex sessions after switching auth
        terminate_all_codex_sessions()

        return True
    except Exception:
        return False


def terminate_all_codex_sessions() -> bool:
    """
    Terminate all running Codex sessions/processes.

    This ensures a clean switch by closing all active Codex
    sessions before the new account takes effect.

    Returns:
        True if successful, False otherwise.
    """
    try:
        # Get current process PID to exclude it
        current_pid = os.getpid()

        # Find all Codex processes
        result = subprocess.run(
            ["pgrep", "-f", "codex"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                if pid:
                    try:
                        pid_int = int(pid)
                        # Skip current process and its parent (the Flask/admin process)
                        if pid_int == current_pid:
                            continue
                        # Try graceful termination first
                        os.kill(pid_int, signal.SIGTERM)
                    except ProcessLookupError:
                        # Process already terminated
                        pass
                    except PermissionError:
                        # Cannot kill process (may be a different user)
                        pass

        return True
    except Exception:
        return False


def get_current_auth_info() -> Optional[Dict[str, Any]]:
    """
    Get current Codex auth information for display.

    Returns:
        Dict with account_id, email, plan_type, or None if not logged in.
    """
    auth = extract_codex_auth()
    if not auth:
        return None

    return {
        "account_id": auth.account_id,
        "email": auth.email,
        "plan_type": auth.plan_type,
        "organization_id": auth.organization_id,
        "organization_name": auth.organization_name,
        "subscription_active_start": auth.subscription_active_start,
        "subscription_active_until": auth.subscription_active_until,
        "subscription_last_checked": auth.subscription_last_checked,
        "is_logged_in": True,
    }


def get_organization_name(api_key: str, account_id: str) -> Optional[str]:
    """
    Get the organization/team name from ChatGPT API.

    Args:
        api_key: Access token for API authentication.
        account_id: Account ID for the request.

    Returns:
        Organization name if available, None otherwise.
    """
    try:
        import requests

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "ChatGPT-Account-Id": account_id,
        }

        # Try to get organization info from the accounts endpoint
        response = requests.get(
            "https://chatgpt.com/backend-api/accounts/me",
            headers=headers,
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            # Try different response formats
            account = data.get("account", {})
            if account:
                return account.get("name") or account.get("title")

            # Alternative format
            return data.get("name") or data.get("title")

    except Exception:
        pass

    return None
