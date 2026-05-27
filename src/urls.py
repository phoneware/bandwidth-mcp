"""Centralized API host resolution.

Production hosts are the defaults. Each host can be overridden at runtime
via its env var (`BW_API_URL`, `BW_VOICE_URL`, `BW_DASHBOARD_URL`,
`BW_MESSAGING_URL`).
"""

from __future__ import annotations

import os

_PROD = {
    "api": "https://api.bandwidth.com",
    "voice": "https://voice.bandwidth.com",
    "dashboard": "https://dashboard.bandwidth.com",
    "messaging": "https://messaging.bandwidth.com",
}

_OVERRIDE_ENV = {
    "api": "BW_API_URL",
    "voice": "BW_VOICE_URL",
    "dashboard": "BW_DASHBOARD_URL",
    "messaging": "BW_MESSAGING_URL",
}


def _resolve(host: str) -> str:
    override = os.environ.get(_OVERRIDE_ENV[host])
    if override:
        return override.rstrip("/")
    return _PROD[host]


def api_base() -> str:
    return _resolve("api")


def voice_base() -> str:
    return _resolve("voice")


def dashboard_base() -> str:
    return _resolve("dashboard")


def messaging_base() -> str:
    return _resolve("messaging")


def oauth_token_url() -> str:
    return f"{api_base()}/api/v1/oauth2/token"


def dashboard_api_base() -> str:
    return f"{dashboard_base()}/api"
