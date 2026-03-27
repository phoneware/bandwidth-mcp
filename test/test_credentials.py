import pytest
from fastmcp import FastMCP


@pytest.mark.asyncio
async def test_set_credentials_tool_registered():
    """setCredentials should be a tool on the MCP server."""
    from src.tools.credentials import register_credentials_tools

    mcp = FastMCP(name="Test")
    config = {}
    register_credentials_tools(mcp, config, reload_callback=None)
    tools = await mcp.get_tools()
    assert "setCredentials" in tools


@pytest.mark.asyncio
async def test_set_credentials_updates_config():
    """setCredentials should update the shared config dict."""
    from src.tools.credentials import set_credentials_flow

    config = {}
    reload_called = []

    async def mock_reload():
        reload_called.append(True)

    result = await set_credentials_flow(
        config=config,
        username="new_user",
        password="new_pass",
        account_id="acct-123",
        reload_callback=mock_reload,
    )

    assert config["BW_USERNAME"] == "new_user"
    assert config["BW_PASSWORD"] == "new_pass"
    assert config["BW_ACCOUNT_ID"] == "acct-123"
    assert result["status"] == "credentials_set"
    assert len(reload_called) == 1
