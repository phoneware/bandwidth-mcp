"""
Phoneware hosted entrypoint for the Bandwidth MCP (streamable-http) with a static
Bearer gate.

Why this exists: in HTTP "hosted mode" the upstream server exposes /mcp with NO
inbound authentication (the env creds BW_CLIENT_ID/BW_CLIENT_SECRET are only used
for the *upstream* Bandwidth OAuth, and setCredentials is stdio-only). An open
/mcp URL would let anyone drive Phoneware's Bandwidth account. This wrapper
requires `Authorization: Bearer $BW_GATEWAY_TOKEN` on the MCP endpoint so only
Phoneware's own Claude connection can reach it.

Bandwidth webhook callback routes and health are intentionally NOT gated -
Bandwidth cannot present our token, and those routes deliver async voice/message
events rather than exposing account control. Only the MCP endpoint is protected.

Run:  BW_MCP_TRANSPORT=streamable-http BW_GATEWAY_TOKEN=... python serve.py
"""
import hmac
import os

import uvicorn

# `app` is installed as a top-level module by `pip install .` (see pyproject
# [tool.setuptools] package-dir "" = "src"). `mcp` is the configured FastMCP
# instance whose lifespan performs the Bandwidth OAuth + tool registration.
from app import mcp

_TOKEN = os.environ.get("BW_GATEWAY_TOKEN", "")
if len(_TOKEN) < 16:
    raise SystemExit(
        "BW_GATEWAY_TOKEN (>= 16 chars) is required to expose the MCP endpoint over HTTP. "
        "Refusing to serve /mcp without a gate."
    )

_PORT = int(os.environ.get("BW_MCP_PORT", "8080"))
# Only these path prefixes require the bearer. Callback webhooks (Bandwidth ->
# us) and health checks stay open. Override with BW_GATEWAY_PROTECT if the MCP
# mount path differs.
_PROTECTED = tuple(
    p.strip() for p in os.environ.get("BW_GATEWAY_PROTECT", "/mcp").split(",") if p.strip()
)
_EXPECTED = f"Bearer {_TOKEN}"


def _authorized(headers) -> bool:
    for k, v in headers or []:
        if k == b"authorization":
            return hmac.compare_digest(v.decode("latin1"), _EXPECTED)
    return False


# Build the streamable-http ASGI app. This carries the FastMCP lifespan (upstream
# OAuth + tool setup) and the registered Bandwidth callback routes.
try:
    _inner = mcp.http_app(transport="streamable-http")
except TypeError:
    # Older/newer FastMCP signatures default to streamable-http.
    _inner = mcp.http_app()


async def app(scope, receive, send):
    """ASGI middleware: gate the MCP endpoint, pass everything else through
    (including the lifespan scope, so the FastMCP lifespan still runs)."""
    if scope.get("type") == "http":
        path = scope.get("path", "") or ""
        if any(path.startswith(p) for p in _PROTECTED) and not _authorized(scope.get("headers")):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"www-authenticate", b'Bearer realm="bandwidth-mcp"'),
                        (b"content-type", b"text/plain; charset=utf-8"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b"Unauthorized"})
            return
    await _inner(scope, receive, send)


if __name__ == "__main__":
    os.environ.setdefault("BW_MCP_TRANSPORT", "streamable-http")
    os.environ.setdefault("BW_MCP_HOST", "0.0.0.0")
    # lifespan="on": uvicorn drives the FastMCP lifespan through our pass-through.
    uvicorn.run(app, host="0.0.0.0", port=_PORT, lifespan="on")
