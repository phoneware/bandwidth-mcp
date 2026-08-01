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
    payload = {"typ": "at", "exp": time.time() + 600, "cid": "CLI-test"}
    payload.update(overrides)
    return serve._sign(payload)


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


@pytest.mark.asyncio(loop_scope="module")
async def test_authorize_returns_code_and_iss(gateway):
    async with _http(gateway.application) as client:
        resp = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": "CLI-abc",
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": _b64u(hashlib.sha256(b"verifier").digest()),
                "code_challenge_method": "S256",
                "state": "st-1",
                "resource": "https://mcp.gateway.test/mcp",
            },
        )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "iss=https%3A%2F%2Fmcp.gateway.test" in location
    assert "state=st-1" in location
    assert "code=" in location


@pytest.mark.asyncio(loop_scope="module")
async def test_authorize_rejects_foreign_resource_indicator(gateway):
    """RFC 8707: a token for someone else's MCP server is not ours to issue."""
    async with _http(gateway.application) as client:
        resp = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": "CLI-abc",
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": _b64u(hashlib.sha256(b"verifier").digest()),
                "code_challenge_method": "S256",
                "resource": "https://evil.example.com/mcp",
            },
        )
    assert resp.status_code == 302
    assert "error=invalid_request" in resp.headers["location"]
    assert "iss=" in resp.headers["location"]


@pytest.mark.asyncio(loop_scope="module")
async def test_token_exchange_binds_the_resource_audience(gateway, monkeypatch):
    minted = {}

    async def fake_mint(client_id, client_secret):
        minted["creds"] = (client_id, client_secret)

    monkeypatch.setattr(serve, "_mint_upstream", fake_mint)

    verifier = "verifier-string"
    code = serve._sign(
        {
            "typ": "code",
            "exp": time.time() + 60,
            "cid": "CLI-abc",
            "ru": "https://claude.ai/api/mcp/auth_callback",
            "cc": _b64u(hashlib.sha256(verifier.encode()).digest()),
            "res": "https://mcp.gateway.test/mcp",
            "n": "abc",
        }
    )
    async with _http(gateway.application) as client:
        resp = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_verifier": verifier,
                "client_id": "CLI-abc",
                "client_secret": "s3cret",
                "resource": "https://mcp.gateway.test/mcp",
            },
        )
    body = resp.json()
    assert resp.status_code == 200
    assert minted["creds"] == ("CLI-abc", "s3cret")
    assert serve._verify(body["access_token"], "at")["aud"] == (
        "https://mcp.gateway.test/mcp"
    )
    assert serve._verify(body["refresh_token"], "rt") is not None


@pytest.mark.asyncio(loop_scope="module")
async def test_token_rejects_foreign_resource_indicator(gateway, monkeypatch):
    monkeypatch.setattr(serve, "_mint_upstream", lambda *a, **k: None)
    async with _http(gateway.application) as client:
        resp = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": "whatever",
                "client_id": "CLI-abc",
                "client_secret": "s3cret",
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
async def test_mcp_401s_until_the_upstream_token_is_minted(gateway, monkeypatch):
    """After a container restart there is no Bandwidth token in memory. The
    gate must 401 so the client refreshes and the mint runs again."""
    monkeypatch.setitem(serve._config, "BW_ACCESS_TOKEN", "")
    async with _http(gateway.application) as client:
        resp = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1},
            headers={"Authorization": f"Bearer {_bearer()}"},
        )
    assert resp.status_code == 401


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
