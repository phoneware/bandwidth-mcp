import asyncio
import os

os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

from fastmcp import FastMCP
from servers import create_bandwidth_mcp
from config import (
    load_config,
    authenticate_config,
    get_enabled_tools,
    get_excluded_tools,
    get_transport_config,
)
from tools.credentials import register_credentials_tools
from tools.callbacks import register_callback_tools
from tools.voice import register_voice_tools
from instructions import build_instructions
from event_store import EventStore
from callbacks import register_callback_routes

mcp = FastMCP(name="Bandwidth MCP")
_config = {}
_event_store = EventStore()


async def setup(mcp: FastMCP = mcp):
    """Setup the Bandwidth MCP server with tools and resources.

    All tools are registered regardless of auth state. If credentials are
    provided (via env vars), OAuth happens at startup and API calls work
    immediately. If not, tools are visible but API calls return 401 until
    the user adds credentials to their MCP config and restarts.
    """
    enabled_tools = get_enabled_tools()
    excluded_tools = get_excluded_tools()
    _config.update(load_config())
    await authenticate_config(_config)

    print("Setting up Bandwidth MCP server...")
    await create_bandwidth_mcp(mcp, enabled_tools, excluded_tools, _config)

    register_credentials_tools(mcp, _config)
    register_callback_tools(mcp, _event_store, _config)
    register_voice_tools(mcp, _event_store)
    register_callback_routes(mcp, _event_store)

    all_tools = await mcp.get_tools()
    mcp.instructions = build_instructions(_config, list(all_tools.keys()))


def main():
    """Main function to run the Bandwidth MCP server."""
    try:
        asyncio.run(setup())

        transport_config = get_transport_config()
        transport = transport_config["transport"]

        if transport == "stdio":
            mcp.run()
        else:
            mcp.run(
                transport=transport,
                host=transport_config["host"],
                port=transport_config["port"],
            )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
