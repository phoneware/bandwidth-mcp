"""Centralized API host resolution.

Production hosts are the defaults. `BW_ENVIRONMENT=test` (or `uat`) flips the
API and Voice hosts to the test environment in one shot, matching the `band`
CLI. Each host can also be overridden individually via its env var
(`BW_API_URL`, `BW_VOICE_URL`, `BW_MESSAGING_URL`); a per-host override wins
over `BW_ENVIRONMENT`.

The Dashboard XML API is served from the API gateway at `{api_base}/api/v2`
— same shape the CLI uses — so there is no separate `BW_DASHBOARD_URL`.
"""

from __future__ import annotations

import os

_PROD = {
    "api": "https://api.bandwidth.com",
    "voice": "https://voice.bandwidth.com",
    "messaging": "https://messaging.bandwidth.com",
}

_TEST = {
    "api": "https://test.api.bandwidth.com",
    "voice": "https://test.voice.bandwidth.com",
    "messaging": "https://messaging.bandwidth.com",
}

_OVERRIDE_ENV = {
    "api": "BW_API_URL",
    "voice": "BW_VOICE_URL",
    "messaging": "BW_MESSAGING_URL",
}


def _resolve(host: str) -> str:
    override = os.environ.get(_OVERRIDE_ENV[host])
    if override:
        return override.rstrip("/")
    env = os.environ.get("BW_ENVIRONMENT", "").lower()
    if env in ("test", "uat"):
        return _TEST[host]
    return _PROD[host]


def api_base() -> str:
    return _resolve("api")


def voice_base() -> str:
    return _resolve("voice")


def messaging_base() -> str:
    return _resolve("messaging")


def oauth_token_url() -> str:
    return f"{api_base()}/api/v1/oauth2/token"


def dashboard_api_base() -> str:
    """Dashboard XML API base — served from the API gateway under /api/v2."""
    return f"{api_base()}/api/v2"
