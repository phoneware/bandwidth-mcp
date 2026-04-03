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
from tools.discovery import register_discovery_tools
from instructions import build_instructions
from event_store import EventStore
from callbacks import register_callback_routes
from tunnel import start_tunnel, stop_tunnel

_config = {}
_event_store = EventStore()


@asynccontextmanager
async def lifespan(mcp_instance: FastMCP):
    """Server lifespan — runs setup inside FastMCP's event loop."""
    enabled_tools = get_enabled_tools()
    excluded_tools = get_excluded_tools()
    _config.update(load_config())
    await authenticate_config(_config)

    # Auto-tunnel for dev: if no public URL is set and we're in HTTP mode,
    # start a cloudflared tunnel so callbacks work without manual setup.
    transport_config = get_transport_config()
    if not _config.get("BW_MCP_BASE_URL") and transport_config["transport"] != "stdio":
        tunnel_url = start_tunnel(transport_config["port"])
        if tunnel_url:
            _config["BW_MCP_BASE_URL"] = tunnel_url

    print("Setting up Bandwidth MCP server...")
    await create_bandwidth_mcp(mcp_instance, enabled_tools, excluded_tools, _config)

    register_credentials_tools(mcp_instance, _config)
    register_callback_tools(mcp_instance, _event_store, _config)
    register_voice_tools(mcp_instance, _event_store, _config)
    register_discovery_tools(mcp_instance, _config)

    # Auto-configure voice app callbacks to current tunnel/base URL
    base_url = _config.get("BW_MCP_BASE_URL")
    voice_app = _config.get("BW_VOICE_APPLICATION_ID")
    if base_url and voice_app and _config.get("BW_ACCESS_TOKEN"):
        from tools.callbacks import configure_callbacks_flow

        try:
            result = await configure_callbacks_flow(_config, voice_app, base_url, ["voice"])
            print(f"Auto-configured voice callbacks → {base_url}")
        except Exception as e:
            print(f"Warning: Failed to auto-configure callbacks: {e}")

    all_tools = await mcp_instance.get_tools()
    mcp_instance.instructions = build_instructions(_config, list(all_tools.keys()))

    yield

    stop_tunnel()


mcp = FastMCP(name="Bandwidth MCP", lifespan=lifespan)

# Register callback HTTP routes at module level — must happen before mcp.run()
# so Starlette includes them in the app's route table.
register_callback_routes(mcp, _event_store)


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
