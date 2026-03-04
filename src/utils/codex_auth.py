"""Codex authentication utilities."""

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any
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
        with open(auth_path, "r") as f:
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
        tokens = auth_json.get("tokens")
        if not tokens:
            return None

        # Get access_token
        access_token = tokens.get("access_token")
        if not access_token:
            return None

        # Get account_id
        account_id = tokens.get("account_id")
        if not account_id:
            # Try to extract from id_token
            id_token = tokens.get("id_token")
            if id_token:
                account_id = _extract_account_id_from_jwt(id_token)

        if not account_id:
            return None

        # Get email and plan_type from JWT
        email = None
        plan_type = None
        organization_id = None
        organization_name = None
        id_token = tokens.get("id_token")
        if id_token:
            claims = _decode_jwt_payload(id_token)
            if claims:
                email = claims.get("email")
                auth_claim = claims.get("https://api.openai.com/auth", {})
                if not account_id:
                    account_id = auth_claim.get("chatgpt_account_id")
                plan_type = auth_claim.get("chatgpt_plan_type")

                # Extract organization info from the "organizations" claim in id_token
                organizations = claims.get("organizations", [])
                if organizations:
                    # Get the default organization (or first one)
                    org = organizations[0]
                    organization_id = org.get("id")
                    organization_name = org.get("title")

        return CodexAuth(
            account_id=account_id,
            access_token=access_token,
            email=email,
            plan_type=plan_type,
            refresh_token=tokens.get("refresh_token"),
            expires_at=tokens.get("expires_at"),
            organization_id=organization_id,
            organization_name=organization_name,
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


def get_codex_account_id() -> Optional[str]:
    """
    Get account ID from Codex auth file.

    Returns:
        Account ID string if available, None otherwise.
    """
    auth_path = get_codex_auth_path()

    if not auth_path.exists():
        return None

    try:
        with open(auth_path, "r") as f:
            auth_data = json.load(f)

        tokens = auth_data.get("tokens", {})
        return tokens.get("account_id")

    except (json.JSONDecodeError, IOError):
        return None


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
