import os
from contextlib import asynccontextmanager

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

_config = {}
_event_store = EventStore()


@asynccontextmanager
async def lifespan(mcp_instance: FastMCP):
    """Server lifespan — runs setup inside FastMCP's event loop."""
    enabled_tools = get_enabled_tools()
    excluded_tools = get_excluded_tools()
    _config.update(load_config())
    await authenticate_config(_config)

    print("Setting up Bandwidth MCP server...")
    await create_bandwidth_mcp(mcp_instance, enabled_tools, excluded_tools, _config)

    register_credentials_tools(mcp_instance, _config)
    register_callback_tools(mcp_instance, _event_store, _config)
    register_voice_tools(mcp_instance, _event_store)
    register_callback_routes(mcp_instance, _event_store)

    all_tools = await mcp_instance.get_tools()
    mcp_instance.instructions = build_instructions(_config, list(all_tools.keys()))

    yield


mcp = FastMCP(name="Bandwidth MCP", lifespan=lifespan)


# For tests that call setup() directly
async def setup(mcp_instance: FastMCP = None):
    """Setup for testing — wraps the lifespan context."""
    if mcp_instance is None:
        mcp_instance = mcp
    async with lifespan(mcp_instance):
        yield mcp_instance


def main():
    """Main function to run the Bandwidth MCP server."""
    transport_config = get_transport_config()
    transport = transport_config["transport"]

    try:
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
