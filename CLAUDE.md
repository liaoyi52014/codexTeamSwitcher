# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an **OpenAI Codex Team Auto-Switching System** that automatically switches between multiple Codex team accounts when the current team's quota falls below 5%. The system monitors usage via the ChatGPT API and performs seamless token switching through a proxy layer.

## Commands

- **Start**: `./run.sh`
- **Status**: `./run.sh --status`
- **Check Usage**: `./run.sh --check`
- **Development**: Python 3.9+ with virtual environment

## Architecture

The system consists of five core services:

1. **Token Manager Service** (`src/services/token_manager.py`) - Manages OAuth tokens for multiple teams (storage, encryption, validation)
2. **Usage Monitor Service** (`src/services/usage_monitor.py`) - Polls usage API every 5 minutes to check quota usage
3. **Team Switcher Service** (`src/services/team_switcher.py`) - Executes team switching when quota < 5%
4. **Proxy Service** (`src/services/proxy.py`) - Flask proxy for seamless request forwarding
5. **Admin Interface** (`src/services/admin.py`) - Web UI for team management

### Key Components
- `src/config/` - Configuration (settings.py)
- `src/services/` - Core services
- `src/models/` - Data models (team.py, switch_log.py)
- `src/utils/` - Utilities (crypto.py for token encryption, logger.py, codex_auth.py)

### Usage API
The system calls `https://chatgpt.com/backend-api/wham/usage` with the account's access token and `ChatGPT-Account-Id` header to get:
- `primary_window`: 5-hour usage window
- `secondary_window`: 1-week usage window

### Data Model
Teams are stored in SQLite with:
- OAuth tokens (encrypted)
- `quota_5h_percentage`, `quota_weekly_percentage` - Usage percentages
- `auth_json` - Full auth.json for account switching
- `priority` and `enabled` flags

### Seamless Switch Implementation
The proxy layer intercepts requests and forwards them to the active team's endpoint. Switching involves:
1. Writing the target team's `auth_json` to `~/.codex/auth.json`
2. Codex automatically uses the new credentials

## Security

- Token encryption (Fernet/AES-256) for storage
- Encryption key persisted in `data/.encryption_key`
- Log sanitization - tokens are redacted in logs
- `src/utils/codex_auth.py` handles auth.json reading/writing

## Auto-Import Feature

On first startup (when no teams configured):
1. Reads `~/.codex/auth.json` to get current account
2. Extracts account_id, email, access_token
3. Stores encrypted token and auth_json in database
4. User can import additional accounts by:
   - Switching teams in Codex CLI (`codex switch-team`)
   - Clicking "导入当前账户" in web UI

## Coding Standards

### Must Follow Rules

- **Type hints required**: All function parameters and return values must have type annotations
- **Docstrings required**: All public functions/classes must have docstrings in Google format
- **Comments required**: Add inline comments for complex logic, business rules, and non-obvious code
- **Error handling**: Always handle exceptions with specific error types, never use bare `except`
- **Constants**: Use UPPER_SNAKE_CASE for constants, group related constants in classes/enums
- **Naming**: Use descriptive names, avoid single-letter variables (except loop variables)

### Comment Style

```python
def get_team_usage(team_token: str) -> dict:
    """
    Get team usage quota by executing /status command.

    Args:
        team_token: OAuth access token for the team

    Returns:
        Dict with 'total', 'used', 'remaining', 'percentage' keys

    Raises:
        TokenExpiredError: If the team token has expired
        StatusCommandError: If /status command fails
    """
    # Token validation before API call to fail fast
    if not self._is_token_valid(team_token):
        raise TokenExpiredError("Team token has expired")

    # Execute status command with team's token
    # This calls Codex CLI internally to get real-time quota
    result = subprocess.run(...)
```

### Code Organization

- Imports: stdlib → third-party → local (alphabetically within groups)
- Class definition order: constants → class variables → __init__ → public methods → private methods
- Keep functions under 50 lines; split larger functions into smaller helpers
