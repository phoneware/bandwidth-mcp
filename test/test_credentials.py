import pytest
from unittest.mock import AsyncMock, patch
from fastmcp import FastMCP


@pytest.mark.asyncio
async def test_set_credentials_tool_registered():
    """setCredentials should be a tool on the MCP server."""
    from src.tools.credentials import register_credentials_tools

    mcp = FastMCP(name="Test")
    config = {}
    register_credentials_tools(mcp, config)
    tools = await mcp.get_tools()
    assert "setCredentials" in tools


@pytest.mark.asyncio
async def test_set_credentials_does_oauth_flow():
    """setCredentials should exchange credentials for a Bearer token and extract accounts."""
    from src.tools.credentials import set_credentials_flow

    config = {}

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
        )

    assert config["BW_CLIENT_ID"] == "CLI-test-id"
    assert config["BW_CLIENT_SECRET"] == "test-secret"
    assert config["BW_ACCESS_TOKEN"] == "test-bearer-token"
    assert config["BW_ACCOUNT_ID"] == "12345"
    assert result["status"] == "credentials_set"
    assert result["accounts"] == ["12345"]
    assert result["active_account"] == "12345"
    mock_oauth.assert_called_once_with("CLI-test-id", "test-secret")


@pytest.mark.asyncio
async def test_set_credentials_no_reload_callback():
    """setCredentials works without a reload callback."""
    from src.tools.credentials import set_credentials_flow

    config = {}
    mock_token_data = {
        "access_token": "test-token",
        "accounts": ["12345"],
        "token_type": "bearer",
    }

    with patch("src.tools.credentials.get_oauth_token", new_callable=AsyncMock) as mock_oauth:
        mock_oauth.return_value = mock_token_data
        result = await set_credentials_flow(config, "CLI-test", "secret")

    assert result["status"] == "credentials_set"
    assert config["BW_ACCESS_TOKEN"] == "test-token"


@pytest.mark.asyncio
async def test_clear_credentials_tool_registered():
    """clearCredentials should be a tool on the MCP server."""
    from src.tools.credentials import register_credentials_tools

    mcp = FastMCP(name="Test")
    config = {}
    register_credentials_tools(mcp, config)
    tools = await mcp.get_tools()
    assert "clearCredentials" in tools


@pytest.mark.asyncio
async def test_clear_credentials_removes_auth_keys():
    """clearCredentials should remove all auth-related keys from config."""
    from src.tools.credentials import clear_credentials_flow

    config = {
        "BW_CLIENT_ID": "CLI-test-id",
        "BW_CLIENT_SECRET": "test-secret",
        "BW_ACCESS_TOKEN": "test-bearer-token",
        "BW_ACCOUNT_ID": "12345",
        "_authenticated_servers_loaded": True,
        "BW_MCP_TRANSPORT": "sse",  # non-auth key should survive
    }

    result = clear_credentials_flow(config)

    assert result["status"] == "logged_out"
    assert "BW_CLIENT_ID" in result["cleared"]
    assert "BW_ACCESS_TOKEN" in result["cleared"]
    assert "BW_CLIENT_ID" not in config
    assert "BW_CLIENT_SECRET" not in config
    assert "BW_ACCESS_TOKEN" not in config
    assert "BW_ACCOUNT_ID" not in config
    assert "_authenticated_servers_loaded" not in config
    assert config["BW_MCP_TRANSPORT"] == "sse"


@pytest.mark.asyncio
async def test_clear_credentials_noop_when_not_logged_in():
    """clearCredentials on an empty config should return empty cleared list."""
    from src.tools.credentials import clear_credentials_flow

    config = {"BW_MCP_TRANSPORT": "sse"}
    result = clear_credentials_flow(config)

    assert result["status"] == "logged_out"
    assert result["cleared"] == []
