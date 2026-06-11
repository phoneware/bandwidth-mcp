"""Centralized API host resolution.

Production hosts are the defaults. `BW_ENVIRONMENT=test` (or `uat`) flips the
API and Voice hosts to the test environment in one shot, matching the `band`
CLI. Each host can also be overridden individually via its env var
(`BW_API_URL`, `BW_VOICE_URL`, `BW_MESSAGING_URL`, `BW_MFA_URL`,
`BW_INSIGHTS_URL`); a per-host override wins over `BW_ENVIRONMENT`.

The Dashboard XML API is served from the API gateway at `{api_base}/api/v2`
— same shape the CLI uses — so there is no separate `BW_DASHBOARD_URL`.

`swap_host(url)` rewrites OpenAPI spec server URLs so the from_openapi tools
respect the same env vars. Without it, the bundled specs would pin every
generated tool to its hardcoded prod host.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

_PROD = {
    "api": "https://api.bandwidth.com",
    "voice": "https://voice.bandwidth.com",
    "messaging": "https://messaging.bandwidth.com",
    "mfa": "https://mfa.bandwidth.com",
    "insights": "https://insights.bandwidth.com",
}

_TEST = {
    "api": "https://test.api.bandwidth.com",
    "voice": "https://test.voice.bandwidth.com",
    "messaging": "https://messaging.bandwidth.com",
    "mfa": "https://mfa.bandwidth.com",
    "insights": "https://insights.bandwidth.com",
}

_OVERRIDE_ENV = {
    "api": "BW_API_URL",
    "voice": "BW_VOICE_URL",
    "messaging": "BW_MESSAGING_URL",
    "mfa": "BW_MFA_URL",
    "insights": "BW_INSIGHTS_URL",
}

# Reverse map for swap_host: known prod hostnames → host key. Includes both
# the "split" hosts (voice.bandwidth.com) and api.bandwidth.com itself, since
# several specs (lookup, voice, TFV, end-user-management) declare api.bandwidth.com
# as their server.
_HOST_TO_KEY = {
    "api.bandwidth.com": "api",
    "voice.bandwidth.com": "voice",
    "messaging.bandwidth.com": "messaging",
    "mfa.bandwidth.com": "mfa",
    "insights.bandwidth.com": "insights",
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


def mfa_base() -> str:
    return _resolve("mfa")


def insights_base() -> str:
    return _resolve("insights")


def oauth_token_url() -> str:
    return f"{api_base()}/api/v1/oauth2/token"


def dashboard_api_base() -> str:
    """Dashboard XML API base — served from the API gateway under /api/v2."""
    return f"{api_base()}/api/v2"


def swap_host(url: str) -> str:
    """Rewrite a spec server URL so it honors BW_ENVIRONMENT / BW_*_URL.

    Takes a full URL (e.g. `https://api.bandwidth.com/v2`), looks up the
    host in `_HOST_TO_KEY`, and rewrites it to whatever `_resolve` returns
    for that host key. Path/query are preserved. Unknown hosts pass through
    untouched.
    """
    parsed = urlparse(url)
    key = _HOST_TO_KEY.get(parsed.netloc)
    if key is None:
        return url
    new_base = _resolve(key)
    new_parsed = urlparse(new_base)
    return urlunparse(
        parsed._replace(scheme=new_parsed.scheme, netloc=new_parsed.netloc)
    )
