"""
TRN Assistant configuration.

Priority order for each value:
  1. Environment variable (set in Docker / docker-compose)
  2. /data/options.json  (Home Assistant addon options, written by HA at startup)
  3. Hard-coded default  (non-secret values only)
"""

import json
import os
from pathlib import Path

_HA_OPTIONS_FILE = Path("/data/options.json")


def _ha_options() -> dict:
    """Load HA addon options if the file exists, else return empty dict."""
    if _HA_OPTIONS_FILE.exists():
        try:
            return json.loads(_HA_OPTIONS_FILE.read_text())
        except Exception:
            pass
    return {}


def _get(name: str, default: str = "") -> str:
    """Return env var → HA options → default, in that order."""
    return os.environ.get(name) or _ha_options().get(name, default)


def _require(name: str) -> str:
    val = _get(name)
    if not val:
        raise RuntimeError(
            f"Missing required config value: {name}. "
            "Set it as an environment variable or in the HA addon options."
        )
    return val


# Dext API
DEXT_API_KEY = lambda: _require("DEXT_API_KEY")
DEXT_API_BASE_URL = _get("DEXT_API_BASE_URL", "<https://api.dext.com/v1>")

# Zoho Books OAuth2
ZOHO_CLIENT_ID = lambda: _require("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = lambda: _require("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = lambda: _require("ZOHO_REFRESH_TOKEN")
ZOHO_ORGANIZATION_ID = lambda: _require("ZOHO_ORGANIZATION_ID")
# UAE Zoho uses the .ae domain
ZOHO_BOOKS_BASE_URL = _get("ZOHO_BOOKS_BASE_URL", "<https://www.zohoapis.ae/books/v3>")
ZOHO_ACCOUNTS_URL = _get("ZOHO_ACCOUNTS_URL", "<https://accounts.zoho.com>")

# Anthropic (Claude)
ANTHROPIC_API_KEY = lambda: _require("ANTHROPIC_API_KEY")
# claude-haiku-4-5-20251001 = fast/cheap; claude-sonnet-4-6 = higher accuracy
CLAUDE_MODEL = _get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
