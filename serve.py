"""
Phoneware hosted gateway for the Bandwidth MCP: an OAuth 2.1 authorization
server in front of the streamable-http transport.

Bandwidth issues server-to-server (client-credentials) API creds with no user
auth of their own, so this server must NOT sit preloaded with those creds
behind a URL. Instead the Bandwidth client id/secret live in the CLIENT's
connector config (claude.ai custom connector "Client ID / Client Secret"
fields) and only transit here during token exchange:

  1. The client discovers this server's OAuth metadata and sends the browser
     to /authorize. We auto-approve (no login page: possession of the
     Bandwidth creds at /token IS the authorization) and return a short-lived
     signed code. The code alone grants nothing.
  2. The client calls POST /token with the code, PKCE verifier, and its
     client id/secret — the Bandwidth API creds. We validate them the only
     way that means anything: a client-credentials exchange against
     Bandwidth's token endpoint. Success mints the upstream access token into
     the shared in-process config (tools attach it per-request; see
     servers.py) and issues our own signed bearer + refresh token.
  3. MCP requests hit /mcp with our bearer. No Bandwidth secret is stored at
     rest here; the upstream token lives in process memory and is re-minted
     on every refresh cycle (refresh_token grant re-presents the creds).

If the container restarts, the first /mcp call 401s (no upstream token in
memory), the client refreshes, and the mint runs again.

Env:
  BW_GATEWAY_TOKEN   HMAC signing key for codes/tokens (Secret Manager;
                     never leaves the server). >= 32 chars.
  BW_MCP_BASE_URL    public base URL (issuer), e.g.
                     https://mcp.bandwidth.phoneware.cloud
  BW_OAUTH_REDIRECT_ALLOW  optional comma list of extra allowed redirect_uri
                     prefixes (claude.ai/claude.com callbacks + localhost are
                     built in).
"""

import base64
import hashlib
import hmac
import json
import os
import secrets as _secrets
import time
from urllib.parse import urlencode, urlparse

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route

os.environ.setdefault("BW_MCP_TRANSPORT", "streamable-http")

from app import mcp, _config  # noqa: E402  upstream FastMCP instance + shared config
from oauth import get_oauth_token, _decode_jwt_payload  # noqa: E402

_KEY = os.environ.get("BW_GATEWAY_TOKEN", "")
if len(_KEY) < 32:
    raise SystemExit(
        "BW_GATEWAY_TOKEN (>= 32 chars) is required: it signs OAuth codes and "
        "bearer tokens. Refusing to serve without it."
    )
_KEY_BYTES = _KEY.encode()

_PORT = int(os.environ.get("BW_MCP_PORT", os.environ.get("PORT", "8080")))
_BASE = (os.environ.get("BW_MCP_BASE_URL") or f"http://localhost:{_PORT}").rstrip("/")

_CODE_TTL = 300           # authorization codes: 5 minutes
_ACCESS_TTL = 50 * 60     # our bearer: refresh comfortably inside the upstream ~1h
_REFRESH_TTL = 60 * 86400

_EXTRA_REDIRECTS = tuple(
    p.strip() for p in os.environ.get("BW_OAUTH_REDIRECT_ALLOW", "").split(",") if p.strip()
)


# ── signed-blob helpers (base64url(json) + HMAC-SHA256) ─────────────────────
def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (4 - len(s) % 4))


def _sign(payload: dict) -> str:
    body = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64u(hmac.new(_KEY_BYTES, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def _verify(token: str, typ: str) -> dict | None:
    try:
        body, sig = token.split(".")
        expect = _b64u(hmac.new(_KEY_BYTES, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(_b64u_dec(body))
        if payload.get("typ") != typ or payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def _redirect_allowed(uri: str) -> bool:
    if any(uri.startswith(p) for p in _EXTRA_REDIRECTS):
        return True
    u = urlparse(uri)
    if u.scheme == "https" and u.hostname in ("claude.ai", "claude.com") and u.path.startswith("/api/mcp/auth_callback"):
        return True
    # Header-capable local clients (Claude Code) use a loopback callback.
    if u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1"):
        return True
    return False


# ── upstream mint ───────────────────────────────────────────────────────────
async def _mint_upstream(client_id: str, client_secret: str) -> None:
    """Validate the presented creds against Bandwidth and load the upstream
    token into the shared config. Raises RuntimeError on rejection."""
    token_data = await get_oauth_token(client_id, client_secret)
    _config["BW_ACCESS_TOKEN"] = token_data["access_token"]
    accounts = token_data.get("accounts") or []
    _config["BW_ACCOUNTS"] = accounts
    if accounts and not os.environ.get("BW_ACCOUNT_ID"):
        _config["BW_ACCOUNT_ID"] = accounts[0]
    try:
        _config["BW_TOKEN_EXP"] = _decode_jwt_payload(token_data["access_token"]).get(
            "exp", time.time() + 3600
        )
    except Exception:
        _config["BW_TOKEN_EXP"] = time.time() + 3600


def _upstream_live() -> bool:
    return bool(_config.get("BW_ACCESS_TOKEN")) and _config.get(
        "BW_TOKEN_EXP", 0
    ) > time.time() + 60


# ── OAuth endpoints ─────────────────────────────────────────────────────────
async def as_metadata(request: Request):
    return JSONResponse(
        {
            "issuer": _BASE,
            "authorization_endpoint": f"{_BASE}/authorize",
            "token_endpoint": f"{_BASE}/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": [
                "client_secret_basic",
                "client_secret_post",
            ],
            "scopes_supported": ["bandwidth"],
        }
    )


async def resource_metadata(request: Request):
    return JSONResponse(
        {
            "resource": _BASE,
            "authorization_servers": [_BASE],
            "bearer_methods_supported": ["header"],
        }
    )


async def authorize(request: Request):
    q = request.query_params
    redirect_uri = q.get("redirect_uri", "")
    if not redirect_uri or not _redirect_allowed(redirect_uri):
        return PlainTextResponse("invalid redirect_uri", status_code=400)
    client_id = q.get("client_id", "")
    challenge = q.get("code_challenge", "")
    if (
        q.get("response_type") != "code"
        or not client_id
        or not challenge
        or q.get("code_challenge_method", "S256") != "S256"
    ):
        params = {"error": "invalid_request", **({"state": q["state"]} if q.get("state") else {})}
        return RedirectResponse(f"{redirect_uri}{'&' if '?' in redirect_uri else '?'}{urlencode(params)}", status_code=302)
    code = _sign(
        {
            "typ": "code",
            "exp": time.time() + _CODE_TTL,
            "cid": client_id,
            "ru": redirect_uri,
            "cc": challenge,
            "n": _secrets.token_hex(8),
        }
    )
    params = {"code": code, **({"state": q["state"]} if q.get("state") else {})}
    return RedirectResponse(f"{redirect_uri}{'&' if '?' in redirect_uri else '?'}{urlencode(params)}", status_code=302)


def _client_auth(request: Request, form) -> tuple[str, str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            raw = base64.b64decode(auth[6:]).decode()
            cid, _, csec = raw.partition(":")
            if cid:
                return cid, csec
        except Exception:
            pass
    return form.get("client_id", ""), form.get("client_secret", "")


def _issue_tokens(client_id: str) -> JSONResponse:
    now = time.time()
    at = _sign({"typ": "at", "exp": now + _ACCESS_TTL, "cid": client_id})
    rt = _sign({"typ": "rt", "exp": now + _REFRESH_TTL, "cid": client_id})
    return JSONResponse(
        {
            "access_token": at,
            "token_type": "Bearer",
            "expires_in": _ACCESS_TTL,
            "refresh_token": rt,
            "scope": "bandwidth",
        },
        headers={"Cache-Control": "no-store"},
    )


def _token_error(error: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status, headers={"Cache-Control": "no-store"})


async def token(request: Request):
    form = await request.form()
    client_id, client_secret = _client_auth(request, form)
    if not client_id or not client_secret:
        return _token_error("invalid_client", 401)
    grant = form.get("grant_type", "")

    if grant == "authorization_code":
        payload = _verify(form.get("code", ""), "code")
        if not payload or payload.get("cid") != client_id or payload.get("ru") != form.get("redirect_uri", ""):
            return _token_error("invalid_grant")
        verifier = form.get("code_verifier", "")
        if _b64u(hashlib.sha256(verifier.encode()).digest()) != payload.get("cc"):
            return _token_error("invalid_grant")
    elif grant == "refresh_token":
        payload = _verify(form.get("refresh_token", ""), "rt")
        if not payload or payload.get("cid") != client_id:
            return _token_error("invalid_grant")
    else:
        return _token_error("unsupported_grant_type")

    # The real authorization: do the presented creds mint a Bandwidth token?
    try:
        await _mint_upstream(client_id, client_secret)
    except RuntimeError:
        return _token_error("invalid_client", 401)
    return _issue_tokens(client_id)


# ── MCP gate ────────────────────────────────────────────────────────────────
_inner = mcp.http_app()  # serves /mcp + Bandwidth callback routes


async def gated(scope, receive, send):
    if scope.get("type") == "http" and (scope.get("path") or "").startswith("/mcp"):
        authz = ""
        for k, v in scope.get("headers") or []:
            if k == b"authorization":
                authz = v.decode("latin1")
                break
        ok = authz.lower().startswith("bearer ") and _verify(authz[7:], "at") is not None
        if not ok or not _upstream_live():
            headers = [
                (
                    b"www-authenticate",
                    f'Bearer resource_metadata="{_BASE}/.well-known/oauth-protected-resource"'.encode(),
                ),
                (b"content-type", b"application/json"),
            ]
            await send({"type": "http.response.start", "status": 401, "headers": headers})
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"error":"invalid_token"}',
                }
            )
            return
    await _inner(scope, receive, send)


application = Starlette(
    routes=[
        Route("/.well-known/oauth-authorization-server", as_metadata),
        Route("/.well-known/oauth-protected-resource", resource_metadata),
        Route("/.well-known/oauth-protected-resource/mcp", resource_metadata),
        Route("/authorize", authorize, methods=["GET"]),
        Route("/token", token, methods=["POST"]),
        Mount("/", app=gated),
    ],
    lifespan=_inner.lifespan,
)

if __name__ == "__main__":
    uvicorn.run(application, host="0.0.0.0", port=_PORT)
