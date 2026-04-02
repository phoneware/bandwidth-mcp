"""OAuth2 client credentials flow for Bandwidth API.

Exchanges client ID + secret for a Bearer token, and extracts account IDs
from the JWT claims — same flow as the Bandwidth CLI.
"""

import base64
import json
from typing import Any

import httpx

TOKEN_URL = "https://api.bandwidth.com/api/v1/oauth2/token"


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode JWT payload without verification (we trust Bandwidth's token endpoint)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT format")
    # JWT base64url → standard base64
    payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
    payload_bytes = base64.urlsafe_b64decode(payload_b64)
    return json.loads(payload_bytes)


async def get_oauth_token(
    client_id: str,
    client_secret: str,
    token_url: str = TOKEN_URL,
) -> dict[str, Any]:
    """Exchange client credentials for a Bearer token.

    Args:
        client_id: Bandwidth API client ID.
        client_secret: Bandwidth API client secret.
        token_url: OAuth2 token endpoint.

    Returns:
        Dict with keys: access_token, accounts (list), token_type.

    Raises:
        RuntimeError: If token exchange fails.
    """
    auth_bytes = f"{client_id}:{client_secret}".encode("utf-8")
    auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            token_url,
            data={"grant_type": "client_credentials"},
            headers={
                "Authorization": f"Basic {auth_b64}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

    if response.status_code != 200:
        raise RuntimeError(
            f"OAuth2 token exchange failed ({response.status_code}): {response.text}"
        )

    token_data = response.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("No access_token in OAuth2 response")

    # Extract account IDs from JWT claims
    claims = _decode_jwt_payload(access_token)
    accounts = claims.get("accounts", [])

    return {
        "access_token": access_token,
        "accounts": accounts,
        "token_type": token_data.get("token_type", "bearer"),
    }
