from pathlib import Path

import specs

from fastmcp import FastMCP
from httpx import AsyncClient
from typing import Dict, List, Optional, Callable, Any

from server_utils import (
    add_resources,
    create_route_map_fn,
    fetch_openapi_spec,
    print_server_info,
)
from urls import swap_host

_SPECS_DIR = Path(specs.__file__).parent

# All API specs. Tools are cherrypicked by profiles, so loading all specs
# is fine — only the operationIds in the active profile get registered.
api_server_info: Dict[str, Dict[str, Any]] = {
    "messaging": {"url": "https://dev.bandwidth.com/spec/messaging.yml"},
    "phone-number-lookup": {
        "url": "https://dev.bandwidth.com/spec/phone-number-lookup-v2.yml"
    },
    "voice": {"url": "https://dev.bandwidth.com/spec/voice.yml"},
    "insights": {
        "url": "https://dev.bandwidth.com/spec/insights.yml",
        # listCalls/listCall collide with voice spec — exclude from insights
        "exclude_tools": ["listCalls", "listCall"],
    },
    "end-user-management": {
        "url": "https://dev.bandwidth.com/spec/end-user-management.yml"
    },
    # Numbers API is XML-based — from_openapi sends JSON which the API rejects.
    # Disabled until we have a proper XML adapter or hand-written tools.
    # "numbers": {"url": "https://dev.bandwidth.com/spec/numbers.yml"},
    "toll-free-verification": {
        "url": "https://dev.bandwidth.com/spec/toll-free-verification.yml"
    },
    "build-registration": {
        "url": str(_SPECS_DIR / "build.yml"),
    },
}


async def _create_server(
    url: str,
    route_map_fn: Optional[Callable] = None,
    config: Optional[Dict[str, Any]] = None,
) -> FastMCP:
    """Create an MCP server from the provided spec URL and credentials.

    Always registers tools regardless of auth state. Auth is checked at
    call time — unauthenticated calls get a 401 from the API, not a
    startup failure.
    """
    if config is None:
        config = {}
    spec_object = await fetch_openapi_spec(url)

    if "servers" not in spec_object or not spec_object["servers"]:
        raise ValueError(f"OpenAPI spec from {url} has no servers defined")

    # Spec server URLs hardcode prod hosts (api/voice/messaging/mfa/insights
    # .bandwidth.com). Rewrite so BW_ENVIRONMENT and per-host overrides take
    # effect for OpenAPI-derived tools too. swap_host preserves the path.
    spec_object["servers"][0]["url"] = swap_host(spec_object["servers"][0]["url"])
    base_url = spec_object["servers"][0]["url"]

    headers = {"User-Agent": "Bandwidth MCP Server"}
    token = config.get("BW_ACCESS_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async def _ensure_content_type(request):
        """Workaround for FastMCP from_openapi: when the OpenAPI spec declares
        a non-JSON request body (e.g. application/xml for updateCallBxml),
        FastMCP sends the body via httpx `content=` without setting Content-Type.
        Bandwidth's APIs reject those with 415. Sniff the body and inject the
        right header before the request goes out.
        """
        if not request.content:
            return
        if request.headers.get("content-type"):
            return
        body_start = request.content[:64].lstrip() if isinstance(request.content, bytes) else request.content[:64].lstrip().encode()
        if body_start.startswith(b"<"):
            request.headers["content-type"] = "application/xml"
        elif body_start.startswith(b"{") or body_start.startswith(b"["):
            request.headers["content-type"] = "application/json"
        else:
            request.headers["content-type"] = "application/octet-stream"

    client = AsyncClient(
        base_url=base_url,
        headers=headers,
        follow_redirects=True,
        event_hooks={"request": [_ensure_content_type]},
    )

    mcp = FastMCP.from_openapi(
        openapi_spec=spec_object,
        client=client,
        name="Bandwidth",
        route_map_fn=route_map_fn,
    )

    return mcp


async def create_bandwidth_mcp(
    mcp: FastMCP,
    enabled_tools: Optional[List[str]],
    excluded_tools: Optional[List[str]],
    config: Optional[Dict[str, Any]] = None,
) -> FastMCP:
    """Create the Bandwidth MCP server from all supplied APIs, taking into account enabled and excluded APIs.

    Args:
        mcp: The FastMCP instance to import servers into
        enabled_tools: List of tools to enable. If None, all tools are enabled.
        excluded_tools: List of tools to exclude. Takes priority over enabled_tools.
        config: Configuration dictionary containing API credentials and other variables.

    Returns:
        The FastMCP instance with all API servers imported and resources added.

    Raises:
        RuntimeError: If any API server fails to create or import
    """
    if config is None:
        config = {}
    route_map_fn = create_route_map_fn(enabled_tools, excluded_tools)

    for api_name, api_info in api_server_info.items():
        try:
            # Merge per-spec exclusions (only when not using explicit enabled_tools)
            spec_excludes = api_info.get("exclude_tools", [])
            if spec_excludes and not enabled_tools:
                combined_excluded = list(set((excluded_tools or []) + spec_excludes))
                spec_route_map_fn = create_route_map_fn(None, combined_excluded)
            else:
                spec_route_map_fn = route_map_fn
            server = await _create_server(
                api_info["url"],
                route_map_fn=spec_route_map_fn,
                config=config,
            )
            await mcp.import_server(server)
        except Exception as e:
            print(f"Warning: Failed to create server for {api_name}: {e}")

    add_resources(mcp, config)
    await print_server_info(mcp)

    return mcp
