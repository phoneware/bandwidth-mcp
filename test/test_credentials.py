import pytest
from unittest.mock import AsyncMock, patch
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
async def test_set_credentials_does_oauth_flow():
    """setCredentials should exchange credentials for a Bearer token and extract accounts."""
    from src.tools.credentials import set_credentials_flow

    config = {}
    reload_called = []

    async def mock_reload():
        reload_called.append(True)

    mock_token_data = {
        "access_token": "test-bearer-token",
        "accounts": ["12345"],
        "token_type": "bearer",
    }

    with patch("src.tools.credentials.get_oauth_token", new_callable=AsyncMock) as mock_oauth:
        mock_oauth.return_value = mock_token_data

        result = await set_credentials_flow(
            config=config,
            client_id="CLI-test-id",
            client_secret="test-secret",
            reload_callback=mock_reload,
        )

    assert config["BW_CLIENT_ID"] == "CLI-test-id"
    assert config["BW_CLIENT_SECRET"] == "test-secret"
    assert config["BW_ACCESS_TOKEN"] == "test-bearer-token"
    assert config["BW_ACCOUNT_ID"] == "12345"
    assert result["status"] == "credentials_set"
    assert result["accounts"] == ["12345"]
    assert result["active_account"] == "12345"
    assert len(reload_called) == 1
    mock_oauth.assert_called_once_with("CLI-test-id", "test-secret")
