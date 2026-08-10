"""Google sign-in for the hosted gateway: establishing *who* is connecting.

Bandwidth issues one account-level `client_credentials` pair with no user auth
of its own, so there is nothing in the carrier API that can tell one caller
from another. Google is not standing in for Bandwidth auth here. Its only job
is to produce a verified email address, which the gateway matches against an
authorized-user list before it will let anyone near the carrier credential.

Everything in this module is a pure function or a single well-marked HTTP call,
so the allowlist and claim-checking logic is testable without a browser.

Mirrors `phoneware/autotask-mcp` `src/auth/google-provider.ts`, which runs the
same pattern in front of Autotask.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any
from urllib.parse import urlencode

import httpx

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Google mints `iss` both with and without the scheme, and has done for years.
_VALID_ISSUERS = ("accounts.google.com", "https://accounts.google.com")

# Small allowance for clock skew between Cloud Run and Google when checking exp.
_SKEW_SECONDS = 60


class GoogleAuthError(Exception):
    """Google refused the exchange, or returned something we will not trust."""


def _b64u_dec(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def decode_id_token(id_token: str) -> dict[str, Any] | None:
    """Read the claims out of a Google id_token.

    The signature is deliberately not checked. This token comes back over TLS
    from a direct server-to-server POST to Google's token endpoint, in exchange
    for a single-use code plus our client secret, so the browser never had an
    opportunity to substitute it. That is the same basis on which Google's own
    libraries skip verification for the authorization-code flow, and it keeps a
    JWKS fetch and key cache out of the request path. `iss`, `aud` and `exp` are
    still checked in `verify_claims` as a guard against a misconfigured client.
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        return None
    try:
        return json.loads(_b64u_dec(parts[1]))
    except Exception:
        return None


def verify_claims(claims: dict[str, Any], expected_aud: str, now: float | None = None) -> str:
    """Return the verified email, or raise GoogleAuthError.

    A caller that gets an email back has been told, by Google, that this person
    controls this address. Anything short of that raises.
    """
    now = time.time() if now is None else now

    if claims.get("iss") not in _VALID_ISSUERS:
        raise GoogleAuthError("id_token has an unexpected issuer")

    aud = claims.get("aud")
    # `aud` is a string for Google's web clients; tolerate the list form too.
    aud_ok = aud == expected_aud or (isinstance(aud, list) and expected_aud in aud)
    if not aud_ok:
        raise GoogleAuthError("id_token was not issued for this client")

    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp + _SKEW_SECONDS < now:
        raise GoogleAuthError("id_token has expired")

    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise GoogleAuthError("id_token carries no email")

    # Google sends this as a bool, but the string form shows up via some paths.
    verified = claims.get("email_verified")
    if verified not in (True, "true"):
        raise GoogleAuthError("email is not verified with Google")

    return email


def is_email_allowed(
    email: str,
    allowed_domains: list[str],
    allowed_emails: list[str] | None = None,
) -> bool:
    """Whether a verified email may reach the carrier credential.

    An explicit address wins outright; otherwise the domain has to be listed.
    Both lists empty means nobody is allowed, which is the correct reading of an
    unconfigured allowlist and the reason the caller refuses to boot without one.
    """
    normalized = email.strip().lower()
    if "@" not in normalized:
        return False
    if any(e.strip().lower() == normalized for e in (allowed_emails or []) if e.strip()):
        return True
    domain = normalized.rsplit("@", 1)[1]
    return any(d.strip().lower() == domain for d in allowed_domains if d.strip())


def auth_url(client_id: str, callback_url: str, state: str) -> str:
    """Where to send the browser to sign in.

    `prompt=select_account` because the common failure here is a person landing
    on the refusal page having been signed in as a personal Google account they
    forgot about; making the picker explicit costs one click and saves the
    support round trip.
    """
    return f"{GOOGLE_AUTH_URL}?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": callback_url,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
    )


async def exchange_code(
    code: str,
    client_id: str,
    client_secret: str,
    callback_url: str,
) -> dict[str, Any]:
    """Trade Google's authorization code for id_token claims.

    Raises GoogleAuthError on anything other than a well-formed id_token.
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": callback_url,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code != 200:
        raise GoogleAuthError(f"Google rejected the code exchange ({response.status_code})")

    try:
        body = response.json()
    except Exception as exc:
        raise GoogleAuthError("Google returned a non-JSON token response") from exc

    id_token = body.get("id_token")
    if not id_token:
        raise GoogleAuthError("Google returned no id_token")

    claims = decode_id_token(id_token)
    if claims is None:
        raise GoogleAuthError("id_token was not a readable JWT")
    return claims
