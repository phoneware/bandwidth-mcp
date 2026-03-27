import asyncio
import os
import warnings

os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

from fastmcp import FastMCP
from servers import create_bandwidth_mcp, api_server_info, _create_server
from config import load_config, get_enabled_tools, get_excluded_tools
from server_utils import create_route_map_fn
from tools.credentials import register_credentials_tools

mcp = FastMCP(name="Bandwidth MCP")
_config = {}


async def _reload_authenticated_servers():
    """Load authenticated API servers after credentials are set mid-session."""
    if _config.get("_authenticated_servers_loaded"):
        return
    _config["_authenticated_servers_loaded"] = True

    enabled_tools = get_enabled_tools()
    excluded_tools = get_excluded_tools()
    route_map_fn = create_route_map_fn(enabled_tools, excluded_tools)

    for api_name, api_info in api_server_info.items():
        requires_auth = api_info.get("requires_auth", True)
        if not requires_auth:
            continue
        try:
            server = await _create_server(
                url=api_info["url"],
                route_map_fn=route_map_fn,
                config=_config,
                requires_auth=True,
            )
            await mcp.import_server(server)
        except Exception as e:
            warnings.warn(f"Failed to load {api_name} after credential update: {e}")


async def setup(mcp: FastMCP = mcp):
    """Setup the Bandwidth MCP server with tools and resources."""
    global _config
    enabled_tools = get_enabled_tools()
    excluded_tools = get_excluded_tools()
    _config = load_config()

    print("Setting up Bandwidth MCP server...")
    await create_bandwidth_mcp(mcp, enabled_tools, excluded_tools, _config)

    register_credentials_tools(mcp, _config, reload_callback=_reload_authenticated_servers)


def main():
    """Main function to run the Bandwidth MCP server."""
    asyncio.run(setup())
    mcp.run()


if __name__ == "__main__":
    main()
