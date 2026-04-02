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

_SPECS_DIR = Path(specs.__file__).parent

# Default API specs — loaded unless a profile overrides them.
# Numbers is opt-in only (343 tools, most niche) via BW_MCP_PROFILE=numbers.
_DEFAULT_SPECS: Dict[str, Dict[str, Any]] = {
    "messaging": {"url": "https://dev.bandwidth.com/spec/messaging.yml"},
    "multi-factor-auth": {
        "url": "https://dev.bandwidth.com/spec/multi-factor-auth.yml"
    },
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
    "toll-free-verification": {
        "url": "https://dev.bandwidth.com/spec/toll-free-verification.yml"
    },
    "express-registration": {
        # Bundled locally — not yet published to dev.bandwidth.com
        "url": str(_SPECS_DIR / "express.yml"),
    },
}

# Opt-in specs — only loaded when explicitly requested via profile or env var.
_OPTIONAL_SPECS: Dict[str, Dict[str, Any]] = {
    "numbers": {"url": "https://dev.bandwidth.com/spec/numbers.yml"},
}


def get_api_server_info(include_numbers: bool = False) -> Dict[str, Dict[str, Any]]:
    """Get the API server info dict, optionally including heavy specs."""
    info = dict(_DEFAULT_SPECS)
    if include_numbers:
        info.update(_OPTIONAL_SPECS)
    return info


# For backward compat with imports
api_server_info = _DEFAULT_SPECS


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

    base_url = spec_object["servers"][0]["url"]

    headers = {"User-Agent": "Bandwidth MCP Server"}
    token = config.get("BW_ACCESS_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    client = AsyncClient(base_url=base_url, headers=headers)

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
