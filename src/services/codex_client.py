"""Codex API client for executing commands and interacting with Codex."""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from pathlib import Path

from src.utils.logger import get_logger


@dataclass
class UsageInfo:
    """Data class for team usage information."""

    total: int
    used: int
    remaining: int
    percentage: float
    last_checked: datetime
    # 5-hour window usage (primary)
    usage_5h_percent: float = 0.0
    # Weekly usage (secondary)
    usage_weekly_percent: float = 0.0
    # Refresh time for 5-hour window (UTC)
    refresh_at_5h: Optional[datetime] = None
    # Refresh time for weekly window (UTC)
    refresh_at_weekly: Optional[datetime] = None


class StatusCommandError(Exception):
    """Raised when /status command execution fails."""
    pass


# Global mock usage for testing (set this to simulate quota)
# Format: {"team_id": percentage}
MOCK_USAGE: dict = {}


def set_mock_usage(team_id: str, percentage: float) -> None:
    """Set mock usage percentage for a team (for testing)."""
    MOCK_USAGE[team_id] = percentage


def clear_mock_usage(team_id: str = None) -> None:
    """Clear mock usage."""
    if team_id:
        MOCK_USAGE.pop(team_id, None)
    else:
        MOCK_USAGE.clear()


class CodexClient:
    """
    Client for interacting with Codex CLI commands.

    Handles execution of /status command to retrieve team quota information.
    """

    # Default path to Codex CLI
    DEFAULT_CODEX_PATH = "codex"

    def __init__(self, codex_path: Optional[str] = None):
        """
        Initialize the Codex client.

        Args:
            codex_path: Path to codex CLI executable. Defaults to 'codex'.
        """
        self._codex_path = codex_path or self.DEFAULT_CODEX_PATH
        self._logger = get_logger(__name__)

    def execute_command(
        self,
        command: str,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess:
        """
        Execute a Codex CLI command.

        Args:
            command: Command to execute (e.g., '/status').
            env: Environment variables to set.
            timeout: Command timeout in seconds.

        Returns:
            CompletedProcess object with stdout, stderr, returncode.

        Raises:
            StatusCommandError: If command fails or times out.
        """
        # Build environment
        exec_env = os.environ.copy()
        if env:
            exec_env.update(env)

        try:
            # Use pexpect for interactive command handling
            import pexpect
            import tempfile
            import shutil

            # Create a temp directory to run from (to avoid trust prompts)
            temp_dir = tempfile.mkdtemp()
            original_dir = os.getcwd()

            try:
                os.chdir(temp_dir)

                # Spawn the process
                cmd_parts = [self._codex_path] + command.split()
                proc = pexpect.spawn(
                    cmd_parts[0],
                    cmd_parts[1:],
                    env=exec_env,
                    timeout=timeout,
                    encoding='utf-8',
                )

                # Log file to capture output
                log_file = tempfile.NamedTemporaryFile(mode='w+', delete=False)
                proc.logfile = log_file

                # Handle interactive prompts in a loop
                max_prompts = 5
                prompt_count = 0

                while prompt_count < max_prompts:
                    try:
                        index = proc.expect([
                            r"Do you trust",
                            r"Yes, continue",
                            r"Continue",
                            r"Press enter to continue",
                            r"› 1\. Yes",
                            pexpect.EOF,
                            pexpect.TIMEOUT,
                        ], timeout=timeout)

                        if index <= 4:
                            # Send "1" to select "Yes, continue" then enter
                            proc.sendline("1")
                            prompt_count += 1
                        else:
                            break

                    except pexpect.TIMEOUT:
                        break
                    except pexpect.EOF:
                        break

                # Give it time to run the command and get output
                import time
                time.sleep(8)

                # Send Ctrl+C to interrupt any running command
                try:
                    proc.sendline(chr(3))  # Ctrl+C
                    time.sleep(1)
                except:
                    pass

                # Try to get more output
                try:
                    proc.expect(pexpect.EOF, timeout=2)
                except:
                    pass

                output = proc.before
                proc.close()

                log_file.close()

                # Read log file for debugging
                with open(log_file.name, 'r', encoding='utf-8', errors='replace') as f:
                    log_content = f.read()

                # Clean up
                os.unlink(log_file.name)

                # Create a mock CompletedProcess
                class MockCompletedProcess:
                    def __init__(self, returncode, stdout, stderr):
                        self.returncode = returncode
                        self.stdout = stdout
                        self.stderr = stderr

                return MockCompletedProcess(proc.exitstatus, output, "")

            finally:
                os.chdir(original_dir)
                shutil.rmtree(temp_dir, ignore_errors=True)

        except ImportError:
            # Fallback to subprocess if pexpect not available
            result = subprocess.run(
                [self._codex_path] + command.split(),
                env=exec_env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result
        except subprocess.TimeoutExpired as e:
            raise StatusCommandError(f"Command timed out after {timeout}s: {command}")
        except FileNotFoundError:
            raise StatusCommandError(f"Codex CLI not found at: {self._codex_path}")
        except Exception as e:
            raise StatusCommandError(f"Failed to execute command: {e}")

    def get_usage(
        self,
        api_key: str,
        timeout: int = 30,
        team_id: str = None,
        account_id: str = None,
    ) -> UsageInfo:
        """
        Get team usage information via OpenAI API.

        Uses the OpenAI API to get organization usage data.

        Args:
            api_key: API key for authentication.
            timeout: Request timeout in seconds.
            team_id: Team ID for mock usage lookup.
            account_id: Optional ChatGPT account id override.

        Returns:
            UsageInfo object with quota details.

        Raises:
            StatusCommandError: If API call fails or output cannot be parsed.
        """
        # Check for mock usage first (for testing)
        if team_id and team_id in MOCK_USAGE:
            percentage = MOCK_USAGE[team_id]
            total = 100000
            remaining = int(total * percentage / 100)
            used = total - remaining

            self._logger.info(
                "mock_usage_returned",
                team_id=team_id,
                percentage=percentage,
            )

            return UsageInfo(
                total=total,
                used=used,
                remaining=remaining,
                percentage=percentage,
                last_checked=datetime.utcnow(),
            )

        # Use Codex/ChatGPT API to get usage
        # This is how codex-tools gets the usage data
        try:
            import requests

            # Set up headers for ChatGPT API
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

            # Get account_id from caller (preferred) or active Codex auth.
            if not account_id:
                from src.utils.codex_auth import get_codex_account_id
                account_id = get_codex_account_id()

            if not account_id:
                self._logger.warning("no_account_id_found")
                # Fallback to CLI
                return self._get_usage_via_cli(api_key, timeout)

            # Try multiple usage API endpoints (same as codex-tools)
            usage_urls = [
                "https://chatgpt.com/backend-api/wham/usage",
                "https://chatgpt.com/api/codex/usage",
            ]

            for url in usage_urls:
                try:
                    # Add account ID to headers
                    request_headers = headers.copy()
                    if account_id:
                        request_headers["ChatGPT-Account-Id"] = account_id

                    response = requests.get(
                        url,
                        headers=request_headers,
                        timeout=timeout,
                    )

                    if response.status_code == 200:
                        data = response.json()
                        return self._parse_codex_usage(data)
                    else:
                        self._logger.debug(
                            "usage_api_response",
                            url=url,
                            status=response.status_code,
                        )

                except Exception as e:
                    self._logger.debug("usage_api_error", url=url, error=str(e))
                    continue

            self._logger.warning("all_usage_apis_failed")

        except ImportError:
            self._logger.warning("requests_not_available")
        except Exception as e:
            self._logger.warning("usage_fetch_error", error=str(e))

        # Fallback: Try CLI approach
        return self._get_usage_via_cli(api_key, timeout)

    def _get_usage_via_cli(self, api_key: str, timeout: int = 30) -> UsageInfo:
        """
        Get usage via CLI command (fallback method).

        Args:
            api_key: API key for authentication.
            timeout: Command timeout in seconds.

        Returns:
            UsageInfo object with quota details.
        """
        # Set API key in environment
        env = {"OPENAI_API_KEY": api_key}

        # Execute /status command
        self._logger.debug("executing_status_command", command="/status")
        result = self.execute_command("/status", env=env, timeout=timeout)

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            raise StatusCommandError(f"/status command failed: {error_msg}")

        # Parse the output
        usage = self._parse_status_output(result.stdout)

        self._logger.info(
            "usage_retrieved",
            total=usage.total,
            used=usage.used,
            remaining=usage.remaining,
            percentage=usage.percentage,
        )

        return usage

    def _parse_api_usage(self, data: dict) -> UsageInfo:
        """
        Parse OpenAI API usage response.

        Args:
            data: API response data.

        Returns:
            UsageInfo object with parsed values.
        """
        try:
            # Extract usage from API response
            # The response format may vary, try different fields
            total_usage = data.get("total_usage", 0)
            # API returns usage in cents (smallest currency unit)
            # Convert to credits assuming $100 limit (example)
            total = 100000  # Default quota
            used = int(total_usage / 100) if total_usage else 0
            remaining = total - used
            percentage = (remaining / total * 100) if total > 0 else 0.0

            return UsageInfo(
                total=total,
                used=used,
                remaining=remaining,
                percentage=percentage,
                last_checked=datetime.utcnow(),
            )
        except Exception as e:
            raise StatusCommandError(f"Failed to parse API usage: {e}")

    def _parse_codex_usage(self, data: dict) -> UsageInfo:
        """
        Parse Codex/ChatGPT usage API response.

        This is the same format as used by codex-tools.
        Response contains:
        - rate_limit.primary_window: 5-hour window
        - rate_limit.secondary_window: 1-week window

        Args:
            data: API response data.

        Returns:
            UsageInfo object with parsed values.
        """
        try:
            # Extract rate limit details
            rate_limit = data.get("rate_limit", {})
            primary_window = rate_limit.get("primary_window", {})
            secondary_window = rate_limit.get("secondary_window", {})

            # Get used_percent from windows
            # Primary window is typically 5 hours, secondary is 1 week
            used_percent_5h = self._parse_percent_value(primary_window.get("used_percent"))
            used_percent_weekly = self._parse_percent_value(
                secondary_window.get("used_percent")
            )
            refresh_at_5h = self._extract_window_refresh_at(primary_window)
            refresh_at_weekly = self._extract_window_refresh_at(secondary_window)

            # Calculate remaining percentage (100 - used)
            # Use 5h as the primary percentage for the main display
            percentage = max(0.0, min(100.0, 100.0 - used_percent_5h))
            weekly_percentage = max(0.0, min(100.0, 100.0 - used_percent_weekly))

            # For total/remaining, we use a default scale since we don't have absolute values
            total = 100000  # Default quota units
            remaining = int(total * percentage / 100)
            used = total - remaining

            self._logger.info(
                "codex_usage_parsed",
                percentage=percentage,
                used_percent_5h=used_percent_5h,
                used_percent_weekly=used_percent_weekly,
                primary_window=primary_window.get("limit_window_seconds"),
                secondary_window=secondary_window.get("limit_window_seconds"),
                refresh_at_5h=refresh_at_5h.isoformat() if refresh_at_5h else None,
                refresh_at_weekly=refresh_at_weekly.isoformat() if refresh_at_weekly else None,
            )

            return UsageInfo(
                total=total,
                used=used,
                remaining=remaining,
                percentage=percentage,
                last_checked=datetime.utcnow(),
                usage_5h_percent=percentage,
                usage_weekly_percent=weekly_percentage,
                refresh_at_5h=refresh_at_5h,
                refresh_at_weekly=refresh_at_weekly,
            )
        except Exception as e:
            raise StatusCommandError(f"Failed to parse Codex usage: {e}")

    @staticmethod
    def _parse_percent_value(value: Any) -> float:
        """
        Parse percentage-like values into float.

        Accepts numeric values and strings such as "34.5" or "34.5%".
        """
        if value is None:
            return 0.0

        if isinstance(value, (int, float)):
            return float(value)

        if isinstance(value, str):
            raw = value.strip()
            if raw.endswith("%"):
                raw = raw[:-1].strip()
            if not raw:
                return 0.0
            try:
                return float(raw)
            except ValueError:
                return 0.0

        return 0.0

    @staticmethod
    def _parse_datetime_value(value: Any) -> Optional[datetime]:
        """Parse timestamp-like values into naive UTC datetime."""
        if value is None:
            return None

        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value
            return value.astimezone(timezone.utc).replace(tzinfo=None)

        if isinstance(value, (int, float)):
            timestamp = float(value)
            # Heuristic: values over 1e12 are likely milliseconds.
            if timestamp > 1_000_000_000_000:
                timestamp /= 1000.0
            try:
                return datetime.utcfromtimestamp(timestamp)
            except (ValueError, OSError):
                return None

        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None

            numeric_match = re.fullmatch(r"\d+(?:\.\d+)?", raw)
            if numeric_match:
                return CodexClient._parse_datetime_value(float(raw))

            normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return None

            if parsed.tzinfo is None:
                return parsed
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)

        return None

    @classmethod
    def _extract_window_refresh_at(cls, window: Dict[str, Any]) -> Optional[datetime]:
        """Extract refresh time from rate-limit window metadata."""
        if not window:
            return None

        timestamp_keys = (
            "resets_at",
            "reset_at",
            "next_reset_at",
            "refresh_at",
            "end_at",
            "window_end_at",
            "resetsAt",
            "resetAt",
            "nextResetAt",
        )
        for key in timestamp_keys:
            refresh_at = cls._parse_datetime_value(window.get(key))
            if refresh_at:
                return refresh_at

        relative_keys = (
            "seconds_until_reset",
            "seconds_to_reset",
            "remaining_seconds",
            "reset_in_seconds",
            "seconds_until_refresh",
            "seconds_to_refresh",
        )
        for key in relative_keys:
            value = window.get(key)
            if value is None:
                continue
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                continue
            if seconds < 0:
                continue
            return datetime.utcnow() + timedelta(seconds=seconds)

        return None

    def _parse_status_output(self, output: str) -> UsageInfo:
        """
        Parse /status command output to extract usage information.

        Supports multiple output formats:
        - JSON: {"quota_total": 100000, "quota_used": 95000, "quota_remaining": 5000}
        - Plain text with patterns like "Remaining: 5000 / 100000"
        - Codex format: "100% left" or "XX% left"

        Args:
            output: Raw command output.

        Returns:
            UsageInfo object with parsed values.

        Raises:
            StatusCommandError: If output cannot be parsed.
        """
        # Try JSON format first
        try:
            # Look for JSON in output
            json_match = re.search(r'\{[^{}]*\}', output, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return self._parse_json_usage(data)
        except (json.JSONDecodeError, ValueError):
            pass

        # Try Codex format: "XX% left" (skip "context left" decorations).
        # Examples:
        # - "gpt-5.3-codex xhigh · 69% left"
        # - "69% left"
        left_matches = []
        for match in re.finditer(r'(\d+(?:\.\d+)?)%\s*left\b', output, re.IGNORECASE):
            trailing = output[match.end(): match.end() + 24].lstrip().lower()
            if trailing.startswith("context"):
                continue
            left_matches.append(float(match.group(1)))

        if left_matches:
            # Prefer the last status-like match from the output stream.
            percentage = left_matches[-1]
            # Assume default quota of 100000 units
            total = 100000
            remaining = int(total * percentage / 100)
            used = total - remaining

            return UsageInfo(
                total=total,
                used=used,
                remaining=remaining,
                percentage=percentage,
                last_checked=datetime.utcnow(),
            )

        # Some Codex variants report "XX% used". Convert to remaining percent.
        used_matches = [
            float(match.group(1))
            for match in re.finditer(r'(\d+(?:\.\d+)?)%\s*used\b', output, re.IGNORECASE)
        ]
        if used_matches:
            used_percent = used_matches[-1]
            percentage = max(0.0, min(100.0, 100.0 - used_percent))
            total = 100000
            remaining = int(total * percentage / 100)
            used = total - remaining

            return UsageInfo(
                total=total,
                used=used,
                remaining=remaining,
                percentage=percentage,
                last_checked=datetime.utcnow(),
            )

        # Try plain text format
        # Example patterns:
        # "Quota: 5000 / 100000 (5%)"
        # "Remaining: 5000 of 100000"
        # "Used: 95000, Remaining: 5000, Total: 100000"

        total_match = re.search(r'(?:total|quota_total)[\s:]+(\d+)', output, re.IGNORECASE)
        used_match = re.search(r'(?:used|quota_used)[\s:]+(\d+)', output, re.IGNORECASE)
        remaining_match = re.search(r'(?:remaining|quota_remaining)[\s:]+(\d+)', output, re.IGNORECASE)

        if total_match and remaining_match:
            total = int(total_match.group(1))
            remaining = int(remaining_match.group(1))
            used = int(used_match.group(1)) if used_match else total - remaining

            percentage = (remaining / total * 100) if total > 0 else 0.0

            return UsageInfo(
                total=total,
                used=used,
                remaining=remaining,
                percentage=percentage,
                last_checked=datetime.utcnow(),
            )

        raise StatusCommandError(f"Could not parse /status output: {output[:200]}")

    def _parse_json_usage(self, data: Dict[str, Any]) -> UsageInfo:
        """
        Parse JSON format usage data.

        Args:
            data: Parsed JSON data.

        Returns:
            UsageInfo object.

        Raises:
            StatusCommandError: If required fields are missing.
        """
        # Normalize field names
        total = data.get("quota_total") or data.get("total") or data.get("quotaLimit")
        used = data.get("quota_used") or data.get("used") or data.get("quotaUsed")
        remaining = data.get("quota_remaining") or data.get("remaining") or data.get("quotaRemaining")

        if total is None:
            raise StatusCommandError("Missing 'total' in response")

        total = int(total)
        remaining = int(remaining) if remaining is not None else 0
        used = int(used) if used is not None else total - remaining

        percentage = (remaining / total * 100) if total > 0 else 0.0

        return UsageInfo(
            total=total,
            used=used,
            remaining=remaining,
            percentage=percentage,
            last_checked=datetime.utcnow(),
        )

    def check_cli_available(self) -> bool:
        """
        Check if Codex CLI is available.

        Returns:
            True if CLI is available, False otherwise.
        """
        try:
            result = subprocess.run(
                [self._codex_path, "--version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False
