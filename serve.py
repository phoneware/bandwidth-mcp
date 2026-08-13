"""
Phoneware hosted gateway for the Bandwidth MCP: an OAuth 2.1 authorization
server, gated on Google sign-in, in front of the streamable-http transport.

Bandwidth issues one account-level server-to-server (client-credentials) pair
with no user auth of its own. A `client_credentials` exchange with that pair
needs no login and returns a token carrying Porting, Ordering, Number
Activation, Billing Reports, Configuration and Regulatory across every account
on it. It is not a gate in front of a credential, it IS the credential, and
whoever holds it can port numbers away or submit disconnects.

So it lives in exactly one place: Secret Manager, mounted into this service.
Never in a repo, a connector field, or a plugin. What stands in front of it is
a real identity check:

  1. A client registers itself at /register (RFC 7591). We mint it a client_id;
     it is a public client, so there is no client secret to leak, and PKCE is
     what binds the exchange. Because `client_id` is ours to mint rather than
     Bandwidth's, dynamic registration works and connectors need no manual
     configuration at all.
  2. The client sends the browser to /authorize. We do NOT auto-approve. We
     stash the request in a signed `state` and hand the browser to Google.
  3. Google returns to /auth/google/callback. We exchange the code for an
     id_token, verify its claims, and match the email against the authorized
     user list (`BW_OAUTH_ALLOWED_DOMAINS` plus optional
     `BW_OAUTH_ALLOWED_EMAILS`). An address that is not on it is refused here
     and never reaches a tool. Only then do we mint our authorization code.
  4. The client calls POST /token with the code and its PKCE verifier. We issue
     our own signed bearer, carrying the verified email so every /mcp call is
     attributable to a person.
  5. /mcp requests present that bearer. The upstream Bandwidth token is minted
     from the server-side creds on demand and refreshed as it ages, so a
     container restart heals itself instead of stranding a connected client.

Only Google ever sees a password, and none passes through this server.

Mirrors `phoneware/autotask-mcp` (`src/auth/google-provider.ts`), which runs
the same pattern in front of Autotask.

Env:
  BW_GATEWAY_TOKEN   HMAC signing key for codes/tokens/client ids (Secret
                     Manager; never leaves the server). >= 32 chars.
  BW_CLIENT_ID       Bandwidth API client id (Secret Manager).
  BW_CLIENT_SECRET   Bandwidth API client secret (Secret Manager).
  BW_OAUTH_CLIENT_ID / BW_OAUTH_CLIENT_SECRET
                     Google OAuth web client for the sign-in gate. Its
                     authorized redirect URI must be
                     BW_MCP_BASE_URL + /auth/google/callback, exactly.
  BW_OAUTH_ALLOWED_DOMAINS   comma list of email domains allowed to sign in.
  BW_OAUTH_ALLOWED_EMAILS    optional comma list of individual addresses.
                     At least one of the two must be non-empty; an empty
                     allowlist would open the carrier account to any Google
                     account on earth, so the server refuses to boot without it.
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
from html import escape
from typing import Any
from urllib.parse import urlencode, urlparse

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from starlette.routing import Mount, Route

os.environ.setdefault("BW_MCP_TRANSPORT", "streamable-http")

from app import mcp, _config  # noqa: E402  upstream FastMCP instance + shared config
from oauth import get_oauth_token, _decode_jwt_payload  # noqa: E402
import gauth  # noqa: E402


def _env_list(name: str) -> list[str]:
    return [p.strip() for p in os.environ.get(name, "").split(",") if p.strip()]


_KEY = os.environ.get("BW_GATEWAY_TOKEN", "")
if len(_KEY) < 32:
    raise SystemExit(
        "BW_GATEWAY_TOKEN (>= 32 chars) is required: it signs OAuth codes, "
        "bearer tokens and client registrations. Refusing to serve without it."
    )
_KEY_BYTES = _KEY.encode()

_PORT = int(os.environ.get("BW_MCP_PORT", os.environ.get("PORT", "8080")))
_BASE = (os.environ.get("BW_MCP_BASE_URL") or f"http://localhost:{_PORT}").rstrip("/")

# ── the carrier credential (server-side, and only here) ─────────────────────
_BW_CLIENT_ID = os.environ.get("BW_CLIENT_ID", "")
_BW_CLIENT_SECRET = os.environ.get("BW_CLIENT_SECRET", "")
if not _BW_CLIENT_ID or not _BW_CLIENT_SECRET:
    raise SystemExit(
        "BW_CLIENT_ID and BW_CLIENT_SECRET are required: the Bandwidth API "
        "credential lives on this service now, not in any client's connector "
        "config. Mount them from Secret Manager. Refusing to serve without them."
    )

# ── the sign-in gate ────────────────────────────────────────────────────────
_GOOGLE_CLIENT_ID = os.environ.get("BW_OAUTH_CLIENT_ID", "")
_GOOGLE_CLIENT_SECRET = os.environ.get("BW_OAUTH_CLIENT_SECRET", "")
if not _GOOGLE_CLIENT_ID or not _GOOGLE_CLIENT_SECRET:
    raise SystemExit(
        "BW_OAUTH_CLIENT_ID and BW_OAUTH_CLIENT_SECRET (a Google OAuth web "
        "client) are required: Google sign-in is the only way in. Refusing to "
        "serve without them."
    )

_ALLOWED_DOMAINS = _env_list("BW_OAUTH_ALLOWED_DOMAINS")
_ALLOWED_EMAILS = _env_list("BW_OAUTH_ALLOWED_EMAILS")
if not _ALLOWED_DOMAINS and not _ALLOWED_EMAILS:
    raise SystemExit(
        "BW_OAUTH_ALLOWED_DOMAINS or BW_OAUTH_ALLOWED_EMAILS must list who may "
        "sign in. An empty allowlist would hand the carrier account to any "
        "Google account on earth. Refusing to serve without one."
    )

_CALLBACK_URL = f"{_BASE}/auth/google/callback"

_CODE_TTL = 300  # authorization codes: 5 minutes
_STATE_TTL = 600  # the Google round trip: 10 minutes
_ACCESS_TTL = 50 * 60  # our bearer: refresh comfortably inside the upstream ~1h
# Public client registrations and refresh credentials have no calendar expiry.
# Existing signed blobs that already carry exp still honor it. Revocation is
# explicit: every refresh and bearer request rechecks the current Google
# allowlist, and rotating BW_GATEWAY_TOKEN invalidates every signed blob.


class RefreshLedgerError(Exception):
    """Durable refresh cursor storage could not complete atomically."""


class RefreshFamilyLedger:
    """Small durable cursor for refresh-token family replay handling.

    The signed refresh token remains the credential. The ledger stores only the
    current and immediately previous cursor fields for a family, so deploys can
    distinguish an immediate duplicate retry from an older replay without
    storing bearer or refresh tokens.
    """

    def start(self, family_id: str, seq: int, jti: str, now: float) -> None:
        raise NotImplementedError

    def rotate(self, family_id: str, seq: int, jti: str, now: float) -> dict[str, Any]:
        raise NotImplementedError


def _family_document(collection: Any, family_id: str) -> Any:
    return collection.document(hashlib.sha256(family_id.encode()).hexdigest())


class FirestoreRefreshFamilyLedger(RefreshFamilyLedger):
    def __init__(self) -> None:
        from google.cloud import firestore

        self._firestore = firestore
        self._client = firestore.Client()
        self._collection = self._client.collection("bandwidth_mcp_refresh_families")

    def start(self, family_id: str, seq: int, jti: str, now: float) -> None:
        ref = _family_document(self._collection, family_id)
        try:
            ref.set(
                {
                    "current_seq": seq,
                    "current_jti": jti,
                    "updated_at": now,
                    "revoked": False,
                }
            )
        except Exception as exc:
            raise RefreshLedgerError from exc

    def rotate(self, family_id: str, seq: int, jti: str, now: float) -> dict[str, Any]:
        ref = _family_document(self._collection, family_id)
        tx = self._client.transaction()

        @self._firestore.transactional
        def _rotate(transaction: Any) -> dict[str, Any]:
            snap = ref.get(transaction=transaction)
            data = snap.to_dict() if snap.exists else {}
            if data.get("revoked"):
                return {"status": "invalid"}

            current_seq = data.get("current_seq")
            current_jti = data.get("current_jti")
            if (
                current_seq is None
                and current_jti is None
                or (current_seq == seq and current_jti == jti)
            ):
                next_seq = seq + 1
                next_jti = _secrets.token_urlsafe(18)
                transaction.set(
                    ref,
                    {
                        "current_seq": next_seq,
                        "current_jti": next_jti,
                        "previous_seq": seq,
                        "previous_jti": jti,
                        "previous_successor_seq": next_seq,
                        "previous_successor_jti": next_jti,
                        "updated_at": now,
                        "revoked": False,
                    },
                )
                return {"status": "rotated", "seq": next_seq, "jti": next_jti}

            if data.get("previous_seq") == seq and data.get("previous_jti") == jti:
                return {
                    "status": "duplicate",
                    "seq": data["previous_successor_seq"],
                    "jti": data["previous_successor_jti"],
                }

            transaction.set(ref, {"revoked": True, "updated_at": now}, merge=True)
            return {"status": "invalid"}

        try:
            return _rotate(tx)
        except Exception as exc:
            raise RefreshLedgerError from exc


class MemoryRefreshFamilyLedger(RefreshFamilyLedger):
    def __init__(self) -> None:
        self.families: dict[str, dict[str, Any]] = {}

    def start(self, family_id: str, seq: int, jti: str, now: float) -> None:
        self.families[family_id] = {
            "current_seq": seq,
            "current_jti": jti,
            "updated_at": now,
            "revoked": False,
        }

    def rotate(self, family_id: str, seq: int, jti: str, now: float) -> dict[str, Any]:
        data = self.families.get(family_id)
        if data is None:
            data = {}
            self.families[family_id] = data
        if data.get("revoked"):
            return {"status": "invalid"}
        current_seq = data.get("current_seq")
        current_jti = data.get("current_jti")
        if (
            current_seq is None
            and current_jti is None
            or (current_seq == seq and current_jti == jti)
        ):
            next_seq = seq + 1
            next_jti = _secrets.token_urlsafe(18)
            data.update(
                {
                    "current_seq": next_seq,
                    "current_jti": next_jti,
                    "previous_seq": seq,
                    "previous_jti": jti,
                    "previous_successor_seq": next_seq,
                    "previous_successor_jti": next_jti,
                    "updated_at": now,
                    "revoked": False,
                }
            )
            return {"status": "rotated", "seq": next_seq, "jti": next_jti}
        if data.get("previous_seq") == seq and data.get("previous_jti") == jti:
            return {
                "status": "duplicate",
                "seq": data["previous_successor_seq"],
                "jti": data["previous_successor_jti"],
            }
        data["revoked"] = True
        data["updated_at"] = now
        return {"status": "invalid"}


_REFRESH_LEDGER: RefreshFamilyLedger | None = None


def _refresh_ledger() -> RefreshFamilyLedger:
    global _REFRESH_LEDGER
    if _REFRESH_LEDGER is None:
        _REFRESH_LEDGER = FirestoreRefreshFamilyLedger()
    return _REFRESH_LEDGER


_EXTRA_REDIRECTS = tuple(_env_list("BW_OAUTH_REDIRECT_ALLOW"))


# ── signed-blob helpers (base64url(json) + HMAC-SHA256) ─────────────────────
def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (4 - len(s) % 4))


def _sign(payload: dict) -> str:
    body = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64u(hmac.new(_KEY_BYTES, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def _verify(token: str, typ: str, *, allow_no_exp: bool = False) -> dict | None:
    try:
        body, sig = token.split(".")
        expect = _b64u(hmac.new(_KEY_BYTES, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(_b64u_dec(body))
        if payload.get("typ") != typ:
            return None
        expires_at = payload.get("exp")
        if expires_at is None:
            if not allow_no_exp:
                return None
        elif not isinstance(expires_at, (int, float)) or expires_at < time.time():
            return None
        return payload
    except Exception:
        return None


def _redirect_allowed(uri: str) -> bool:
    """Where a registered client is allowed to send the browser afterwards.

    This is the policy that stops a registration from being a phishing vector:
    /register is open by design (that is what DCR means), so without it anyone
    could register redirect_uri=https://evil.example and collect a real
    phoneware.us sign-in.
    """
    if any(uri.startswith(p) for p in _EXTRA_REDIRECTS):
        return True
    u = urlparse(uri)
    if (
        u.scheme == "https"
        and u.hostname in ("claude.ai", "claude.com")
        and u.path.startswith("/api/mcp/auth_callback")
    ):
        return True
    # Header-capable local clients (Claude Code) use a loopback callback.
    if u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1"):
        return True
    return False


# ── upstream mint (from OUR creds, never the caller's) ──────────────────────
async def _mint_upstream() -> None:
    """Load a Bandwidth access token into the shared config from the
    server-side credential. Raises RuntimeError if Bandwidth rejects it."""
    token_data = await get_oauth_token(_BW_CLIENT_ID, _BW_CLIENT_SECRET)
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
    return (
        bool(_config.get("BW_ACCESS_TOKEN"))
        and _config.get("BW_TOKEN_EXP", 0) > time.time() + 60
    )


async def _ensure_upstream() -> bool:
    """Mint or refresh the upstream token if it is missing or nearly expired.

    Called on the /mcp path, so a cold container or an expired token heals
    itself rather than 401ing a client that did nothing wrong. Under the old
    model this could not be done: the creds only existed for the instant a
    client presented them at /token.
    """
    if _upstream_live():
        return True
    try:
        await _mint_upstream()
    except Exception:
        return False
    return _upstream_live()


# ── OAuth endpoints ─────────────────────────────────────────────────────────
async def as_metadata(request: Request):
    return JSONResponse(
        {
            "issuer": _BASE,
            "authorization_endpoint": f"{_BASE}/authorize",
            "token_endpoint": f"{_BASE}/token",
            "registration_endpoint": f"{_BASE}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            # Public clients: PKCE binds the exchange, so there is no client
            # secret for an MCP client to store or leak.
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["bandwidth"],
            # RFC 9207: we return `iss` on every authorization response, so
            # clients can bind the code to the issuer they recorded. The
            # 2026-07-28 MCP authorization spec has clients validating this.
            "authorization_response_iss_parameter_supported": True,
        }
    )


async def resource_metadata(request: Request):
    return JSONResponse(
        {
            "resource": _BASE,
            "authorization_servers": [_BASE],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["bandwidth"],
            "resource_name": "Bandwidth MCP (Phoneware)",
        }
    )


def _resource_ok(resource: str) -> bool:
    """RFC 8707 resource indicator check.

    Clients name the MCP server they want a token for. We only reject a
    clearly foreign origin: claude.ai and Claude Code disagree on whether the
    indicator carries the /mcp path or a trailing slash, and rejecting on that
    would break a working connector for no security gain."""
    if not resource:
        return True
    u = urlparse(resource)
    base = urlparse(_BASE)
    return (u.scheme, u.hostname, u.port) == (base.scheme, base.hostname, base.port)


# ── dynamic client registration (RFC 7591) ──────────────────────────────────
async def register(request: Request):
    """Mint a client_id for a client that asks for one.

    Open by design: that is what dynamic registration is for, and it is what
    lets a connector work with nothing filled in. The registration grants
    nothing on its own. It records a set of redirect URIs, each of which had to
    pass `_redirect_allowed`, and every real authorization still requires a
    Google sign-in by somebody on the allowlist.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {
                "error": "invalid_client_metadata",
                "error_description": "body must be JSON",
            },
            status_code=400,
        )

    redirect_uris = body.get("redirect_uris")
    if (
        not isinstance(redirect_uris, list)
        or not redirect_uris
        or not all(isinstance(u, str) and u for u in redirect_uris)
    ):
        return JSONResponse(
            {
                "error": "invalid_redirect_uri",
                "error_description": "redirect_uris must be a non-empty array of strings",
            },
            status_code=400,
        )

    bad = [u for u in redirect_uris if not _redirect_allowed(u)]
    if bad:
        return JSONResponse(
            {
                "error": "invalid_redirect_uri",
                "error_description": f"redirect_uri not permitted: {bad[0]}",
            },
            status_code=400,
        )

    client_id = _sign(
        {
            "typ": "cli",
            "ru": redirect_uris,
            "n": _secrets.token_hex(8),
        }
    )
    return JSONResponse(
        {
            "client_id": client_id,
            "client_id_issued_at": int(time.time()),
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            # Public client: no secret issued, PKCE is the binding.
            "token_endpoint_auth_method": "none",
            **(
                {"client_name": body["client_name"]}
                if isinstance(body.get("client_name"), str)
                else {}
            ),
        },
        status_code=201,
        headers={"Cache-Control": "no-store"},
    )


def _client_redirects(client_id: str) -> list[str] | None:
    payload = _verify(client_id, "cli", allow_no_exp=True)
    if not payload:
        return None
    uris = payload.get("ru")
    return uris if isinstance(uris, list) else None


def _error_redirect(
    redirect_uri: str, error: str, state: str | None
) -> RedirectResponse:
    params = {"error": error, "iss": _BASE, **({"state": state} if state else {})}
    joiner = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        f"{redirect_uri}{joiner}{urlencode(params)}", status_code=302
    )


async def authorize(request: Request):
    """Start the sign-in. Nothing is approved here any more."""
    q = request.query_params
    redirect_uri = q.get("redirect_uri", "")
    client_id = q.get("client_id", "")

    # Before we will bounce a browser anywhere, the client must be one we
    # registered and the target must be one it registered with.
    registered = _client_redirects(client_id) if client_id else None
    if registered is None:
        return PlainTextResponse(
            "unknown client_id: register at /register first", status_code=401
        )
    if (
        not redirect_uri
        or redirect_uri not in registered
        or not _redirect_allowed(redirect_uri)
    ):
        return PlainTextResponse("invalid redirect_uri", status_code=400)

    challenge = q.get("code_challenge", "")
    resource = q.get("resource", "")
    state = q.get("state")
    # `iss` rides on every response, success or error (RFC 9207).
    if (
        q.get("response_type") != "code"
        or not challenge
        or q.get("code_challenge_method", "S256") != "S256"
        or not _resource_ok(resource)
    ):
        return _error_redirect(redirect_uri, "invalid_request", state)

    pending = _sign(
        {
            "typ": "pend",
            "exp": time.time() + _STATE_TTL,
            "cid": client_id,
            "ru": redirect_uri,
            "cc": challenge,
            "res": resource,
            "st": state or "",
            "n": _secrets.token_hex(8),
        }
    )
    return RedirectResponse(
        gauth.auth_url(_GOOGLE_CLIENT_ID, _CALLBACK_URL, pending), status_code=302
    )


def _refused(message: str, status: int = 403) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8>"
        "<title>Sign-in refused</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:34rem;"
        "margin:15vh auto;padding:0 1.5rem;color:#12263a}"
        "h1{font-size:1.25rem;margin:0 0 .5rem}p{margin:.5rem 0;color:#44607a}</style>"
        f"<h1>Sign-in refused</h1><p>{message}</p>"
        "<p>If you believe this is wrong, ask a Phoneware administrator to add "
        "you to the Bandwidth MCP allowlist.</p>",
        status_code=status,
    )


async def google_callback(request: Request):
    """Google is done with the person. Decide whether they get in."""
    q = request.query_params

    pending = _verify(q.get("state", ""), "pend")
    if not pending:
        return _refused(
            "This sign-in link has expired. Start again from your client.", 400
        )

    redirect_uri = pending["ru"]
    client_state = pending.get("st") or None

    if q.get("error"):
        return _error_redirect(redirect_uri, "access_denied", client_state)

    code = q.get("code", "")
    if not code:
        return _error_redirect(redirect_uri, "invalid_request", client_state)

    try:
        claims = await gauth.exchange_code(
            code, _GOOGLE_CLIENT_ID, _GOOGLE_CLIENT_SECRET, _CALLBACK_URL
        )
        email = gauth.verify_claims(claims, _GOOGLE_CLIENT_ID)
    except gauth.GoogleAuthError:
        # Deliberately not echoing Google's message: it is not the person's
        # problem to debug and it can carry request detail.
        return _refused("Google could not verify that sign-in.", 401)

    if not gauth.is_email_allowed(email, _ALLOWED_DOMAINS, _ALLOWED_EMAILS):
        return _refused(
            f"<strong>{escape(email)}</strong> is not authorized to use the "
            "Phoneware Bandwidth connector."
        )

    our_code = _sign(
        {
            "typ": "code",
            "exp": time.time() + _CODE_TTL,
            "cid": pending["cid"],
            "ru": redirect_uri,
            "cc": pending["cc"],
            "res": pending.get("res", ""),
            "sub": email,
            "n": _secrets.token_hex(8),
        }
    )
    params = {
        "code": our_code,
        "iss": _BASE,
        **({"state": client_state} if client_state else {}),
    }
    joiner = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        f"{redirect_uri}{joiner}{urlencode(params)}", status_code=302
    )


def _access_token(client_id: str, subject: str, resource: str, now: float) -> str:
    aud = {"aud": resource} if resource else {}
    return _sign(
        {
            "typ": "at",
            "exp": now + _ACCESS_TTL,
            "cid": client_id,
            "sub": subject,
            **aud,
        }
    )


def _refresh_token(
    client_id: str,
    subject: str,
    resource: str,
    family_id: str,
    seq: int,
    jti: str,
) -> str:
    aud = {"aud": resource} if resource else {}
    return _sign(
        {
            "typ": "rt",
            "fid": family_id,
            "seq": seq,
            "jti": jti,
            "cid": client_id,
            "sub": subject,
            **aud,
        }
    )


def _token_response(access_token: str, refresh_token: str) -> JSONResponse:
    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": _ACCESS_TTL,
            "refresh_token": refresh_token,
            "scope": "bandwidth",
        },
        headers={"Cache-Control": "no-store"},
    )


def _new_token_response(client_id: str, subject: str, resource: str) -> JSONResponse:
    now = time.time()
    family_id = _secrets.token_urlsafe(18)
    seq = 0
    jti = _secrets.token_urlsafe(18)
    _refresh_ledger().start(family_id, seq, jti, now)
    return _token_response(
        _access_token(client_id, subject, resource, now),
        _refresh_token(client_id, subject, resource, family_id, seq, jti),
    )


def _rotated_token_response(
    client_id: str, subject: str, resource: str, refresh_from: dict
) -> JSONResponse:
    now = time.time()
    rotation = _refresh_ledger().rotate(
        refresh_from["fid"], refresh_from["seq"], refresh_from["jti"], now
    )
    if rotation["status"] == "invalid":
        raise ValueError("refresh token replay")
    return _token_response(
        _access_token(client_id, subject, resource, now),
        _refresh_token(
            client_id,
            subject,
            resource,
            refresh_from["fid"],
            rotation["seq"],
            rotation["jti"],
        ),
    )


def _token_error(
    error: str, status: int = 400, description: str | None = None
) -> JSONResponse:
    body = {
        "error": error,
        **({"error_description": description} if description else {}),
    }
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def _valid_refresh_payload(refresh_token: str, client_id: str) -> dict | None:
    payload = _verify(refresh_token, "rt", allow_no_exp=True)
    if not payload or payload.get("cid") != client_id:
        return None
    family_id = payload.get("fid")
    refresh_seq = payload.get("seq")
    token_id = payload.get("jti")
    if family_id is None and refresh_seq is None and token_id is None:
        # Tokens already issued by the deployed public-client gateway did not
        # carry a family cursor. Accept a still-valid public refresh credential
        # and roll it into the current family format so rollout does not force
        # re-registration.
        legacy_family = _b64u(
            hmac.new(_KEY_BYTES, refresh_token.encode(), hashlib.sha256).digest()
        )
        return {
            **payload,
            "fid": f"legacy:{legacy_family}",
            "seq": 0,
            "jti": "legacy",
        }
    if (
        not isinstance(family_id, str)
        or not family_id
        or not isinstance(refresh_seq, int)
        or refresh_seq < 0
        or not isinstance(token_id, str)
        or not token_id
    ):
        return None
    return payload


async def token(request: Request):
    form = await request.form()
    # Public clients: the client_id is the signed registration and PKCE binds
    # the exchange. A non-empty client_secret means an old client-credential
    # registration is still cached; fail deterministically so the client
    # re-registers instead of treating the Bandwidth secret as usable here.
    client_id = form.get("client_id", "")
    client_id = client_id if isinstance(client_id, str) else ""
    secret_present = bool(form.get("client_secret"))
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode()
            basic_client_id, _, basic_secret = decoded.partition(":")
            secret_present = secret_present or bool(basic_secret)
            if not client_id:
                client_id = basic_client_id
        except Exception:
            # A malformed Authorization header must not erase a valid
            # form-encoded public client_id.
            pass
    if secret_present:
        return _token_error(
            "invalid_client",
            401,
            "public clients must register again without a client secret",
        )
    if not client_id or _client_redirects(client_id) is None:
        return _token_error(
            "invalid_client",
            401,
            "unknown client_id: register at /register first",
        )

    grant = form.get("grant_type", "")
    # RFC 8707: the client names the MCP server it wants this token for.
    resource = form.get("resource", "")
    if not _resource_ok(resource):
        return _token_error("invalid_target")

    if grant == "authorization_code":
        payload = _verify(form.get("code", ""), "code")
        if (
            not payload
            or payload.get("cid") != client_id
            or payload.get("ru") != form.get("redirect_uri", "")
        ):
            return _token_error("invalid_grant")
        verifier = form.get("code_verifier", "")
        if _b64u(hashlib.sha256(verifier.encode()).digest()) != payload.get("cc"):
            return _token_error("invalid_grant")
    elif grant == "refresh_token":
        payload = _valid_refresh_payload(form.get("refresh_token", ""), client_id)
        if not payload:
            return _token_error("invalid_grant")
    else:
        return _token_error("unsupported_grant_type")

    # Identity is established by now: a signed code only exists because someone
    # on the allowlist signed in with Google. The carrier credential is ours and
    # is never presented by the client, so the only thing left is to be sure we
    # can actually reach Bandwidth before handing out a bearer that implies we can.
    subject = payload.get("sub", "")
    if not subject:
        return _token_error("invalid_grant")
    if not gauth.is_email_allowed(subject, _ALLOWED_DOMAINS, _ALLOWED_EMAILS):
        return _token_error("invalid_grant", description="authorization revoked")
    if not await _ensure_upstream():
        return _token_error("temporarily_unavailable", 503)

    try:
        if grant == "refresh_token":
            return _rotated_token_response(
                client_id,
                subject,
                resource or payload.get("res", "") or payload.get("aud", ""),
                payload,
            )
        return _new_token_response(
            client_id, subject, resource or payload.get("res", "")
        )
    except RefreshLedgerError:
        return _token_error("temporarily_unavailable", 503)
    except ValueError:
        return _token_error("invalid_grant")


# ── MCP gate ────────────────────────────────────────────────────────────────
# 2026-07-28 requests are sessionless by protocol (no initialize handshake, no
# Mcp-Session-Id) whatever this flag says. stateless_http=True extends the same
# treatment to handshake-era clients, so every POST from every era is
# self-contained. That matches what this server is: request/response tools with
# no server-initiated traffic, nothing that needs a live connection. It also
# means a container restart no longer leaves a client holding a session id the
# server has never heard of.
_inner = mcp.http_app(stateless_http=True)  # serves /mcp + Bandwidth callbacks


async def gated(scope, receive, send):
    if scope.get("type") == "http" and (scope.get("path") or "").startswith("/mcp"):
        authz = ""
        for k, v in scope.get("headers") or []:
            if k == b"authorization":
                authz = v.decode("latin1")
                break
        claims = (
            _verify(authz[7:], "at") if authz.lower().startswith("bearer ") else None
        )
        subject = claims.get("sub", "") if claims else ""
        subject_allowed = bool(subject) and gauth.is_email_allowed(
            subject, _ALLOWED_DOMAINS, _ALLOWED_EMAILS
        )
        if not subject_allowed:
            headers = [
                (
                    b"www-authenticate",
                    f'Bearer resource_metadata="{_BASE}/.well-known/oauth-protected-resource"'.encode(),
                ),
                (b"content-type", b"application/json"),
            ]
            await send(
                {"type": "http.response.start", "status": 401, "headers": headers}
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"error":"invalid_token"}',
                }
            )
            return
        # A valid bearer with a transient upstream failure is retryable service
        # unavailability, not an invalid-token challenge.
        if not await _ensure_upstream():
            headers = [
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
            ]
            await send(
                {"type": "http.response.start", "status": 503, "headers": headers}
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"error":"temporarily_unavailable"}',
                }
            )
            return
    await _inner(scope, receive, send)


application = Starlette(
    routes=[
        Route("/.well-known/oauth-authorization-server", as_metadata),
        Route("/.well-known/oauth-protected-resource", resource_metadata),
        Route("/.well-known/oauth-protected-resource/mcp", resource_metadata),
        Route("/register", register, methods=["POST"]),
        Route("/authorize", authorize, methods=["GET"]),
        Route("/auth/google/callback", google_callback, methods=["GET"]),
        Route("/token", token, methods=["POST"]),
        Mount("/", app=gated),
    ],
    lifespan=_inner.lifespan,
)

if __name__ == "__main__":
    uvicorn.run(application, host="0.0.0.0", port=_PORT)
