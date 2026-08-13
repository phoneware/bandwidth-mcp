"""End-to-end tests for the hosted OAuth gateway (serve.py).

Everything here drives the real Starlette app in-process through an ASGI
transport, with the app's lifespan running, so it exercises what Cloud Run
actually serves: the OAuth metadata, the authorize/token dance, the bearer
gate, and MCP traffic over BOTH protocol eras — the sessionless 2026-07-28
revision and the handshake era that came before it.

The gateway had no test coverage at all before the FastMCP 4 upgrade, which
is exactly the code a protocol-layer swap puts at risk.
"""

import asyncio
import base64
import hashlib
import json
import os
import time
from urllib.parse import unquote

import pytest
import pytest_asyncio

import httpx2  # noqa: E402
from fastmcp.client import Client  # noqa: E402
from fastmcp.client.transports import StreamableHttpTransport  # noqa: E402

# serve.py reads its signing key and issuer at import time, and sets
# BW_MCP_TRANSPORT itself. Put those in place for the import, then hand the
# environment back exactly as it was: other test modules assert on the stdio
# defaults (setCredentials is stdio-only) and must not inherit ours.
_ENV_FOR_IMPORT = {
    "BW_GATEWAY_TOKEN": "x" * 40,
    "BW_MCP_BASE_URL": "https://mcp.gateway.test",
    "BW_MCP_TRANSPORT": "streamable-http",
    # The carrier credential lives on the server now, so serve.py refuses to
    # import without it, the Google client, and an allowlist.
    "BW_CLIENT_ID": "CLI-server-side",
    "BW_CLIENT_SECRET": "server-side-secret",
    "BW_OAUTH_CLIENT_ID": "google-client-id.apps.googleusercontent.com",
    "BW_OAUTH_CLIENT_SECRET": "google-client-secret",
    "BW_OAUTH_ALLOWED_DOMAINS": "phoneware.us",
    "BW_OAUTH_ALLOWED_EMAILS": "contractor@example.com",
}
_SAVED_ENV = {k: os.environ.get(k) for k in _ENV_FOR_IMPORT}
os.environ.update(_ENV_FOR_IMPORT)

import app as app_mod  # noqa: E402  the module serve.py itself imports
import serve  # noqa: E402

for _k, _v in _SAVED_ENV.items():
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


async def _no_openapi(mcp_instance, enabled_tools, excluded_tools, config=None):
    return mcp_instance


async def _no_upstream_auth(config):
    return None


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def gateway():
    """The real gateway app with its lifespan run.

    The lifespan owns anyio task groups, which must be entered and exited from
    the SAME task, so it runs inside one long-lived task here rather than
    across fixture setup/teardown. OpenAPI spec loading is stubbed out (it
    needs the network, and this file is about the gateway, not Bandwidth's
    specs); the hand-written tools still register, so /mcp serves a real tool
    surface.
    """
    ready, stop = asyncio.Event(), asyncio.Event()

    async def run_lifespan():
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(app_mod, "create_bandwidth_mcp", _no_openapi)
            # The carrier creds are real env vars now, so app.py's startup
            # authenticate_config() would fire a live client_credentials call at
            # Bandwidth on every test run. Stub it: this file is about the
            # gateway, and a hermetic suite must not depend on Bandwidth being
            # reachable (or on those creds still being valid).
            mp.setattr(app_mod, "authenticate_config", _no_upstream_auth)
            mp.setenv("BW_MCP_PROFILE", "numbers")
            for key, value in _ENV_FOR_IMPORT.items():
                mp.setenv(key, value)
            async with serve.application.router.lifespan_context(serve.application):
                ready.set()
                await stop.wait()

    task = asyncio.create_task(run_lifespan())
    await ready.wait()
    yield serve
    stop.set()
    await task


def _bearer(**overrides) -> str:
    payload = {
        "typ": "at",
        "exp": time.time() + 600,
        "cid": "CLI-test",
        "sub": "rickw@phoneware.us",
    }
    payload.update(overrides)
    return serve._sign(payload)


_CLAUDE_CB = "https://claude.ai/api/mcp/auth_callback"


def _registered_client(redirect_uri: str = _CLAUDE_CB) -> str:
    """A client_id as /register would mint it, without the round trip."""
    return serve._sign(
        {
            "typ": "cli",
            "exp": time.time() + 3600,
            "ru": [redirect_uri],
            "n": "test",
        }
    )


@pytest.fixture(autouse=True)
def refresh_ledger(monkeypatch):
    ledger = serve.MemoryRefreshFamilyLedger()
    monkeypatch.setattr(serve, "_REFRESH_LEDGER", ledger)
    return ledger


class _FakeSnapshot:
    def __init__(self, data):
        self.exists = bool(data)
        self._data = data

    def to_dict(self):
        return dict(self._data)


class _FakeDocument:
    def __init__(self):
        self.data = {}

    def get(self, transaction=None):
        return _FakeSnapshot(self.data)

    def set(self, data):
        self.data = dict(data)


class _FakeCollection:
    def __init__(self, document):
        self.document_ref = document

    def document(self, _document_id):
        return self.document_ref


class _FakeTransaction:
    def set(self, document, data, merge=False):
        if merge:
            document.data.update(data)
        else:
            document.data = dict(data)


class _FakeClient:
    def __init__(self, transaction):
        self.transaction_ref = transaction

    def transaction(self):
        return self.transaction_ref


class _FakeFirestore:
    @staticmethod
    def transactional(operation):
        return operation


def _firestore_refresh_ledger():
    document = _FakeDocument()
    transaction = _FakeTransaction()
    ledger = serve.FirestoreRefreshFamilyLedger.__new__(
        serve.FirestoreRefreshFamilyLedger
    )
    ledger._firestore = _FakeFirestore()
    ledger._client = _FakeClient(transaction)
    ledger._collection = _FakeCollection(document)
    return ledger


@pytest.mark.parametrize(
    "ledger_factory",
    [serve.MemoryRefreshFamilyLedger, _firestore_refresh_ledger],
)
def test_duplicate_refresh_retry_window_is_bounded_and_revokes_stale_replay(
    ledger_factory,
):
    ledger = ledger_factory()
    rotated_at = 1_700_000_000.0
    ledger.start("family", 0, "original", rotated_at - 1)

    rotated = ledger.rotate("family", 0, "original", rotated_at)
    assert rotated["status"] == "rotated"

    immediate_retry = ledger.rotate(
        "family",
        0,
        "original",
        rotated_at + serve._REFRESH_RETRY_WINDOW,
    )
    assert immediate_retry == {
        "status": "duplicate",
        "seq": rotated["seq"],
        "jti": rotated["jti"],
    }

    stale_replay = ledger.rotate(
        "family",
        0,
        "original",
        rotated_at + serve._REFRESH_RETRY_WINDOW + 1,
    )
    assert stale_replay == {"status": "invalid"}

    successor_after_replay = ledger.rotate(
        "family",
        rotated["seq"],
        rotated["jti"],
        rotated_at + serve._REFRESH_RETRY_WINDOW + 2,
    )
    assert successor_after_replay == {"status": "invalid"}


def _upstream_is_live(monkeypatch) -> None:
    """Pretend Bandwidth has already handed us a token."""
    monkeypatch.setattr(serve, "_ensure_upstream", _always_live)


async def _always_live() -> bool:
    return True


def _fake_jwt(ttl: int = 3600) -> str:
    """A token shaped enough for oauth._decode_jwt_payload to read an exp."""
    claims = _b64u(json.dumps({"exp": int(time.time() + ttl)}).encode())
    return f"{_b64u(b'{}')}.{claims}.{_b64u(b'sig')}"


def _upstream_alive(monkeypatch):
    """Pretend the Bandwidth token mint already happened."""
    monkeypatch.setitem(serve._config, "BW_ACCESS_TOKEN", "upstream-token")
    monkeypatch.setitem(serve._config, "BW_TOKEN_EXP", time.time() + 3600)


def _http(app):
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://gateway.test"
    )


async def _rpc(app, method, params=None, bearer=None):
    """One raw 2026-07-28 JSON-RPC call, no SDK in the way.

    Self-describing request: the protocol version rides in `_meta`, there is
    no prior handshake and no session id. Returns (response, parsed result).
    """
    body = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": method,
        "params": {
            **(params or {}),
            # The whole envelope is required on a modern request: version,
            # who is calling, and what the client can do. There is no earlier
            # handshake to have said any of it.
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {
                    "name": "GatewayTest",
                    "version": "1.0.0",
                },
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
        "Mcp-Name": "bandwidth-mcp",
    }
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    async with _http(app) as client:
        resp = await client.post("/mcp", json=body, headers=headers)
    payload = None
    if resp.status_code == 200:
        text = resp.text
        if resp.headers.get("content-type", "").startswith("text/event-stream"):
            for line in text.splitlines():
                if line.startswith("data:"):
                    payload = json.loads(line[5:].strip())
                    break
        else:
            payload = resp.json()
    return resp, payload


def _mcp_client(app, bearer, mode):
    transport = StreamableHttpTransport(
        url="http://gateway.test/mcp",
        headers={"Authorization": f"Bearer {bearer}"},
        httpx_client_factory=lambda **kw: httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), **kw
        ),
    )
    return Client(transport, mode=mode)


# ── OAuth surface ───────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="module")
async def test_authorization_server_metadata_advertises_iss_support(gateway):
    async with _http(gateway.application) as client:
        resp = await client.get("/.well-known/oauth-authorization-server")
    body = resp.json()
    assert resp.status_code == 200
    assert body["issuer"] == "https://mcp.gateway.test"
    assert body["code_challenge_methods_supported"] == ["S256"]
    # RFC 9207, expected by the 2026-07-28 authorization spec
    assert body["authorization_response_iss_parameter_supported"] is True
    # DCR is the whole reason a connector needs nothing filled in. Losing this
    # is what produced "Automatic client registration isn't supported".
    assert body["registration_endpoint"] == "https://mcp.gateway.test/register"
    # Public clients: PKCE binds the exchange, there is no secret to leak.
    assert body["token_endpoint_auth_methods_supported"] == ["none"]


@pytest.mark.asyncio(loop_scope="module")
async def test_protected_resource_metadata(gateway):
    async with _http(gateway.application) as client:
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        ):
            body = (await client.get(path)).json()
            assert body["resource"] == "https://mcp.gateway.test"
            assert body["authorization_servers"] == ["https://mcp.gateway.test"]
            assert body["scopes_supported"] == ["bandwidth"]


# ── dynamic client registration ─────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="module")
async def test_register_mints_a_usable_public_client(gateway):
    async with _http(gateway.application) as client:
        resp = await client.post(
            "/register",
            json={
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "client_name": "Claude",
            },
        )
    body = resp.json()
    assert resp.status_code == 201
    assert body["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in body
    # The id is a signed blob, so it survives a redeploy with nothing stored.
    assert serve._client_redirects(body["client_id"]) == [
        "https://claude.ai/api/mcp/auth_callback"
    ]
    assert "client_secret_expires_at" not in body
    client_payload = serve._verify(body["client_id"], "cli", allow_no_exp=True)
    assert client_payload is not None
    assert "exp" not in client_payload


@pytest.mark.asyncio(loop_scope="module")
async def test_register_refuses_a_redirect_it_would_not_honour(gateway):
    """/register is open by design, which is exactly why the redirect policy
    has to hold here: otherwise anyone could register their own callback and
    collect a real phoneware.us sign-in."""
    async with _http(gateway.application) as client:
        resp = await client.post(
            "/register", json={"redirect_uris": ["https://evil.example/steal"]}
        )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_redirect_uri"


# ── authorize now starts a Google sign-in, it does not approve ──────────────


@pytest.mark.asyncio(loop_scope="module")
async def test_authorize_hands_the_browser_to_google(gateway):
    async with _http(gateway.application) as client:
        resp = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": _registered_client(),
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": _b64u(hashlib.sha256(b"verifier").digest()),
                "code_challenge_method": "S256",
                "state": "st-1",
                "resource": "https://mcp.gateway.test/mcp",
            },
        )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=google-client-id.apps.googleusercontent.com" in location
    # No authorization code is handed out before anyone has signed in.
    assert "code=" not in location.split("?", 1)[1].replace("response_type=code", "")


@pytest.mark.asyncio(loop_scope="module")
async def test_authorize_rejects_an_unregistered_client(gateway):
    """The old model let any client_id through, because the id was the
    Bandwidth credential. Now an unknown id is simply not a client."""
    async with _http(gateway.application) as client:
        resp = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": "CLI-abc",
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": _b64u(hashlib.sha256(b"verifier").digest()),
                "code_challenge_method": "S256",
            },
        )
    assert resp.status_code == 401


@pytest.mark.asyncio(loop_scope="module")
async def test_authorize_rejects_a_redirect_the_client_never_registered(gateway):
    async with _http(gateway.application) as client:
        resp = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": _registered_client("http://localhost:5000/callback"),
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": _b64u(hashlib.sha256(b"verifier").digest()),
                "code_challenge_method": "S256",
            },
        )
    assert resp.status_code == 400


@pytest.mark.asyncio(loop_scope="module")
async def test_authorize_rejects_foreign_resource_indicator(gateway):
    """RFC 8707: a token for someone else's MCP server is not ours to issue."""
    async with _http(gateway.application) as client:
        resp = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": _registered_client(),
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": _b64u(hashlib.sha256(b"verifier").digest()),
                "code_challenge_method": "S256",
                "resource": "https://evil.example.com/mcp",
            },
        )
    assert resp.status_code == 302
    assert "error=invalid_request" in resp.headers["location"]
    assert "iss=" in resp.headers["location"]


# ── the Google callback is where authorization actually happens ─────────────


def _pending_state(**overrides) -> str:
    payload = {
        "typ": "pend",
        "exp": time.time() + 300,
        "cid": _registered_client(),
        "ru": "https://claude.ai/api/mcp/auth_callback",
        "cc": _b64u(hashlib.sha256(b"verifier").digest()),
        "res": "https://mcp.gateway.test/mcp",
        "st": "st-1",
        "n": "test",
    }
    payload.update(overrides)
    return serve._sign(payload)


def _google_returns(monkeypatch, email: str, verified=True) -> None:
    async def fake_exchange(code, client_id, client_secret, callback_url):
        return {
            "iss": "https://accounts.google.com",
            "aud": "google-client-id.apps.googleusercontent.com",
            "exp": time.time() + 600,
            "email": email,
            "email_verified": verified,
        }

    monkeypatch.setattr(serve.gauth, "exchange_code", fake_exchange)


@pytest.mark.asyncio(loop_scope="module")
async def test_callback_issues_a_code_for_an_allowed_email(gateway, monkeypatch):
    _google_returns(monkeypatch, "rickw@phoneware.us")
    async with _http(gateway.application) as client:
        resp = await client.get(
            "/auth/google/callback",
            params={"code": "g-code", "state": _pending_state()},
        )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://claude.ai/api/mcp/auth_callback?")
    assert "state=st-1" in location
    assert "iss=https%3A%2F%2Fmcp.gateway.test" in location
    code = unquote(location.split("code=", 1)[1].split("&")[0])
    # The verified identity is carried on the code, so the bearer can name a person.
    assert serve._verify(code, "code")["sub"] == "rickw@phoneware.us"


@pytest.mark.asyncio(loop_scope="module")
async def test_callback_refuses_an_email_outside_the_allowlist(gateway, monkeypatch):
    """The point of the whole change: a valid Google account is not enough."""
    _google_returns(monkeypatch, "someone@gmail.com")
    async with _http(gateway.application) as client:
        resp = await client.get(
            "/auth/google/callback",
            params={"code": "g-code", "state": _pending_state()},
        )
    assert resp.status_code == 403
    assert "someone@gmail.com" in resp.text
    # It refuses in place; it does not hand a code back to the client.
    assert "location" not in resp.headers


@pytest.mark.asyncio(loop_scope="module")
async def test_callback_allows_an_explicitly_listed_outside_address(
    gateway, monkeypatch
):
    _google_returns(monkeypatch, "contractor@example.com")
    async with _http(gateway.application) as client:
        resp = await client.get(
            "/auth/google/callback",
            params={"code": "g-code", "state": _pending_state()},
        )
    assert resp.status_code == 302


@pytest.mark.asyncio(loop_scope="module")
async def test_callback_refuses_an_unverified_google_email(gateway, monkeypatch):
    _google_returns(monkeypatch, "rickw@phoneware.us", verified=False)
    async with _http(gateway.application) as client:
        resp = await client.get(
            "/auth/google/callback",
            params={"code": "g-code", "state": _pending_state()},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio(loop_scope="module")
async def test_callback_refuses_a_forged_state(gateway, monkeypatch):
    _google_returns(monkeypatch, "rickw@phoneware.us")
    async with _http(gateway.application) as client:
        resp = await client.get(
            "/auth/google/callback", params={"code": "g-code", "state": "not.signed"}
        )
    assert resp.status_code == 400


# ── token exchange ──────────────────────────────────────────────────────────


def _signed_code(verifier: str, client_id: str, **overrides) -> str:
    payload = {
        "typ": "code",
        "exp": time.time() + 60,
        "cid": client_id,
        "ru": "https://claude.ai/api/mcp/auth_callback",
        "cc": _b64u(hashlib.sha256(verifier.encode()).digest()),
        "res": "https://mcp.gateway.test/mcp",
        "sub": "rickw@phoneware.us",
        "n": "abc",
    }
    payload.update(overrides)
    return serve._sign(payload)


async def _register_via_http(client) -> str:
    resp = await client.post("/register", json={"redirect_uris": [_CLAUDE_CB]})
    assert resp.status_code == 201
    return resp.json()["client_id"]


async def _exchange_code_via_http(client, client_id: str, verifier: str) -> dict:
    resp = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": _signed_code(verifier, client_id),
            "redirect_uri": _CLAUDE_CB,
            "code_verifier": verifier,
            "client_id": client_id,
            "resource": "https://mcp.gateway.test/mcp",
        },
    )
    assert resp.status_code == 200
    return resp.json()


async def _refresh_via_http(client, client_id: str, refresh_token: str):
    return await client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "resource": "https://mcp.gateway.test/mcp",
        },
    )


@pytest.mark.asyncio(loop_scope="module")
async def test_token_exchange_binds_the_resource_and_the_person(gateway, monkeypatch):
    _upstream_is_live(monkeypatch)
    verifier = "verifier-string"
    cid = _registered_client()
    async with _http(gateway.application) as client:
        resp = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": _signed_code(verifier, cid),
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_verifier": verifier,
                "client_id": cid,
                "resource": "https://mcp.gateway.test/mcp",
            },
        )
    body = resp.json()
    assert resp.status_code == 200
    access = serve._verify(body["access_token"], "at")
    assert access["aud"] == "https://mcp.gateway.test/mcp"
    # Every /mcp call is now attributable to a person, not to "whoever has the key".
    assert access["sub"] == "rickw@phoneware.us"
    refresh_payload = serve._verify(body["refresh_token"], "rt", allow_no_exp=True)
    assert refresh_payload is not None
    assert "exp" not in refresh_payload


@pytest.mark.asyncio(loop_scope="module")
async def test_public_dcr_client_refreshes_with_rotation_and_rejects_replay(
    gateway, monkeypatch
):
    _upstream_is_live(monkeypatch)

    async with _http(gateway.application) as client:
        client_id = await _register_via_http(client)
        first = await _exchange_code_via_http(client, client_id, "refresh-verifier")

        first_refresh = first["refresh_token"]
        refresh_resp = await _refresh_via_http(client, client_id, first_refresh)
        assert refresh_resp.status_code == 200
        second_refresh = refresh_resp.json()["refresh_token"]
        assert second_refresh != first_refresh
        second_payload = serve._verify(second_refresh, "rt", allow_no_exp=True)
        assert second_payload is not None
        assert "exp" not in second_payload

        duplicate_resp = await _refresh_via_http(client, client_id, first_refresh)
        assert duplicate_resp.status_code == 200
        assert duplicate_resp.json()["refresh_token"] == second_refresh

        successor_resp = await _refresh_via_http(client, client_id, second_refresh)
        assert successor_resp.status_code == 200
        third_refresh = successor_resp.json()["refresh_token"]
        assert third_refresh != second_refresh

        duplicate_successor = await _refresh_via_http(client, client_id, second_refresh)
        assert duplicate_successor.status_code == 200
        assert duplicate_successor.json()["refresh_token"] == third_refresh

        older_replay = await _refresh_via_http(client, client_id, first_refresh)
        assert older_replay.status_code == 400
        assert older_replay.json()["error"] == "invalid_grant"

        invalid_resp = await _refresh_via_http(client, client_id, "not.signed")
        assert invalid_resp.status_code == 400
        assert invalid_resp.json()["error"] == "invalid_grant"


@pytest.mark.asyncio(loop_scope="module")
async def test_refresh_upstream_failure_is_retryable_and_preserves_refresh_token(
    gateway, monkeypatch
):
    _upstream_is_live(monkeypatch)

    async with _http(gateway.application) as client:
        client_id = await _register_via_http(client)
        first = await _exchange_code_via_http(client, client_id, "flaky-upstream")
        refresh_token = first["refresh_token"]

        attempts = 0

        async def flaky_upstream():
            nonlocal attempts
            attempts += 1
            return attempts > 1

        monkeypatch.setattr(serve, "_ensure_upstream", flaky_upstream)

        failed = await _refresh_via_http(client, client_id, refresh_token)
        assert failed.status_code == 503
        assert failed.json()["error"] == "temporarily_unavailable"

        retried = await _refresh_via_http(client, client_id, refresh_token)
        assert retried.status_code == 200
        assert retried.json()["refresh_token"] != refresh_token


@pytest.mark.asyncio(loop_scope="module")
async def test_deployed_public_refresh_token_upgrades_without_re_registration(
    gateway, monkeypatch
):
    _upstream_is_live(monkeypatch)

    async with _http(gateway.application) as client:
        client_id = await _register_via_http(client)
        deployed_format_refresh = serve._sign(
            {
                "typ": "rt",
                "exp": time.time() + 600,
                "cid": client_id,
                "sub": "rickw@phoneware.us",
                "aud": "https://mcp.gateway.test/mcp",
            }
        )

        upgraded = await _refresh_via_http(client, client_id, deployed_format_refresh)
        assert upgraded.status_code == 200
        upgraded_refresh = serve._verify(
            upgraded.json()["refresh_token"], "rt", allow_no_exp=True
        )
        assert upgraded_refresh["fid"].startswith("legacy:")
        assert upgraded_refresh["seq"] == 1

        replay = await _refresh_via_http(client, client_id, deployed_format_refresh)
        assert replay.status_code == 200
        assert replay.json()["refresh_token"] == upgraded.json()["refresh_token"]


@pytest.mark.asyncio(loop_scope="module")
async def test_refresh_and_bearer_recheck_current_allowlist(gateway, monkeypatch):
    _upstream_is_live(monkeypatch)

    async with _http(gateway.application) as client:
        client_id = await _register_via_http(client)
        tokens = await _exchange_code_via_http(client, client_id, "revocation-verifier")

        monkeypatch.setattr(serve, "_ALLOWED_DOMAINS", ())
        monkeypatch.setattr(serve, "_ALLOWED_EMAILS", ())

        refresh = await _refresh_via_http(client, client_id, tokens["refresh_token"])
        assert refresh.status_code == 400
        assert refresh.json()["error"] == "invalid_grant"
        assert "revoked" in refresh.json()["error_description"]

        bearer, _ = await _rpc(
            gateway.application, "tools/list", bearer=tokens["access_token"]
        )
        assert bearer.status_code == 401


@pytest.mark.asyncio(loop_scope="module")
async def test_legacy_client_credential_registration_gets_re_registration_signal(
    gateway, monkeypatch
):
    _upstream_is_live(monkeypatch)

    async with _http(gateway.application) as client:
        client_id = await _register_via_http(client)
        resp = await client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": "stale-retired-secret",
                "refresh_token": "not.used",
            },
        )

    body = resp.json()
    assert resp.status_code == 401
    assert body["error"] == "invalid_client"
    assert "register" in body["error_description"]
    assert "client secret" in body["error_description"]


@pytest.mark.asyncio(loop_scope="module")
async def test_malformed_basic_auth_does_not_wipe_form_client_id(gateway, monkeypatch):
    _upstream_is_live(monkeypatch)
    verifier = "malformed-basic"

    async with _http(gateway.application) as client:
        client_id = await _register_via_http(client)
        resp = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": _signed_code(verifier, client_id),
                "redirect_uri": _CLAUDE_CB,
                "code_verifier": verifier,
                "client_id": client_id,
            },
            headers={"Authorization": "Basic not-base64"},
        )

    assert resp.status_code == 200


@pytest.mark.asyncio(loop_scope="module")
async def test_token_never_asks_the_client_for_a_carrier_credential(
    gateway, monkeypatch
):
    """A client presenting no secret at all must succeed: that is the change.
    The carrier credential is the server's, and it is not on this wire."""
    captured = {}

    async def fake_get_token(client_id, client_secret, token_url=None):
        captured["creds"] = (client_id, client_secret)
        return {"access_token": _fake_jwt(), "accounts": ["5011369"]}

    monkeypatch.setattr(serve, "get_oauth_token", fake_get_token)
    monkeypatch.setitem(serve._config, "BW_ACCESS_TOKEN", "")
    monkeypatch.setitem(serve._config, "BW_TOKEN_EXP", 0)

    verifier = "v2"
    cid = _registered_client()
    async with _http(gateway.application) as client:
        resp = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": _signed_code(verifier, cid),
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_verifier": verifier,
                "client_id": cid,
            },
        )
    assert resp.status_code == 200
    # Minted from the server's own env creds, never from anything the client sent.
    assert captured["creds"] == ("CLI-server-side", "server-side-secret")


@pytest.mark.asyncio(loop_scope="module")
async def test_token_rejects_an_unregistered_client(gateway, monkeypatch):
    _upstream_is_live(monkeypatch)
    async with _http(gateway.application) as client:
        resp = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": "whatever",
                "client_id": "CLI-abc",
                "client_secret": "s3cret",
            },
        )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_client"


@pytest.mark.asyncio(loop_scope="module")
async def test_token_rejects_a_code_minted_for_another_client(gateway, monkeypatch):
    _upstream_is_live(monkeypatch)
    verifier = "v3"
    theirs = serve._sign(
        {"typ": "cli", "exp": time.time() + 3600, "ru": [_CLAUDE_CB], "n": "theirs"}
    )
    mine = serve._sign(
        {"typ": "cli", "exp": time.time() + 3600, "ru": [_CLAUDE_CB], "n": "mine"}
    )
    assert theirs != mine
    async with _http(gateway.application) as client:
        resp = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": _signed_code(verifier, theirs),
                "redirect_uri": _CLAUDE_CB,
                "code_verifier": verifier,
                "client_id": mine,
            },
        )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


@pytest.mark.asyncio(loop_scope="module")
async def test_token_rejects_foreign_resource_indicator(gateway, monkeypatch):
    _upstream_is_live(monkeypatch)
    async with _http(gateway.application) as client:
        resp = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": "whatever",
                "client_id": _registered_client(),
                "resource": "https://evil.example.com/mcp",
            },
        )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_target"


# ── the bearer gate ─────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="module")
async def test_mcp_requires_our_bearer(gateway, monkeypatch):
    _upstream_alive(monkeypatch)
    async with _http(gateway.application) as client:
        resp = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1})
    assert resp.status_code == 401
    assert "oauth-protected-resource" in resp.headers["www-authenticate"]


@pytest.mark.asyncio(loop_scope="module")
async def test_mcp_rejects_a_forged_bearer(gateway, monkeypatch):
    _upstream_alive(monkeypatch)
    forged = _bearer()[:-4] + "aaaa"
    async with _http(gateway.application) as client:
        resp = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1},
            headers={"Authorization": f"Bearer {forged}"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio(loop_scope="module")
async def test_mcp_reports_retryable_upstream_failure_without_token_challenge(
    gateway, monkeypatch
):
    """A valid bearer with transient upstream failure remains a valid bearer."""

    async def unavailable():
        return False

    monkeypatch.setattr(serve, "_ensure_upstream", unavailable)
    async with _http(gateway.application) as client:
        resp = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1},
            headers={"Authorization": f"Bearer {_bearer()}"},
        )
    assert resp.status_code == 503
    assert resp.json()["error"] == "temporarily_unavailable"
    assert "www-authenticate" not in {k.lower() for k in resp.headers}
    assert resp.headers["cache-control"] == "no-store"


@pytest.mark.asyncio(loop_scope="module")
async def test_bandwidth_callbacks_stay_open(gateway):
    """Bandwidth can't present our bearer; its webhooks must not be gated."""
    async with _http(gateway.application) as client:
        resp = await client.post(
            "/callbacks/messaging/inbound",
            json=[{"type": "message-received", "message": {"id": "m1"}}],
        )
    assert resp.status_code == 200


# ── both protocol eras ──────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="module")
async def test_serves_the_stateless_2026_protocol(gateway, monkeypatch):
    """The 2026-07-28 revision: no initialize handshake, no Mcp-Session-Id,
    every request self-describing. server/discover is mandatory."""
    _upstream_alive(monkeypatch)
    async with _mcp_client(gateway.application, _bearer(), "2026-07-28") as client:
        tools = await client.list_tools()
    names = {t.name for t in tools}
    assert "listPortInOrders" in names
    assert "createPortInOrder" not in names  # numbers profile is read-only


@pytest.mark.asyncio(loop_scope="module")
async def test_discover_reports_versions_and_identity(gateway, monkeypatch):
    """server/discover is mandatory in 2026-07-28: one call returns the
    versions, capabilities, and identity a client would otherwise probe for."""
    _upstream_alive(monkeypatch)
    resp, payload = await _rpc(gateway.application, "server/discover", bearer=_bearer())
    assert resp.status_code == 200
    result = payload["result"]
    assert "2026-07-28" in result["supportedVersions"]
    assert "tools" in result["capabilities"]
    assert result["resultType"] == "complete"
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"]


@pytest.mark.asyncio(loop_scope="module")
async def test_tools_list_carries_cache_hints(gateway, monkeypatch):
    """CacheableResult (SEP-2549): the surface is fixed at startup by the
    deployment's profile, so clients are told they may hold it briefly."""
    _upstream_alive(monkeypatch)
    _, payload = await _rpc(gateway.application, "tools/list", bearer=_bearer())
    result = payload["result"]
    assert result["ttlMs"] == 300_000
    assert result["cacheScope"] == "private"


@pytest.mark.asyncio(loop_scope="module")
async def test_results_identify_the_server(gateway, monkeypatch):
    """Servers SHOULD identify themselves in each result's _meta now that no
    handshake ever did it."""
    _upstream_alive(monkeypatch)
    _, payload = await _rpc(gateway.application, "tools/list", bearer=_bearer())
    info = payload["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]
    assert info["name"] == "Bandwidth MCP"
    assert info["version"] == app_mod.SERVER_VERSION


@pytest.mark.asyncio(loop_scope="module")
async def test_still_serves_handshake_era_clients(gateway, monkeypatch):
    """claude.ai and Claude Code will not all move at once. The handshake era
    has to keep working against the same endpoint."""
    _upstream_alive(monkeypatch)
    async with _mcp_client(gateway.application, _bearer(), "legacy") as client:
        tools = await client.list_tools()
    assert "listPortInOrders" in {t.name for t in tools}


@pytest.mark.asyncio(loop_scope="module")
async def test_no_session_header_is_issued(gateway, monkeypatch):
    """Stateless: nothing hands the client a session to lose on restart."""
    _upstream_alive(monkeypatch)
    # A bare tools/list with no prior handshake: it works, and the response
    # hands back nothing the client would have to carry forward.
    resp, payload = await _rpc(gateway.application, "tools/list", bearer=_bearer())
    assert resp.status_code == 200
    assert "mcp-session-id" not in {k.lower() for k in resp.headers}
    assert payload["result"]["tools"]
