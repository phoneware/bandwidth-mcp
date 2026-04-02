"""Integration tests verifying the full MCP server setup."""

import pytest
from unittest.mock import AsyncMock, patch
from fastmcp import FastMCP
from pytest_httpx import HTTPXMock
from utils import create_mock


@pytest.mark.asyncio
async def test_instructions_set_after_setup(httpx_mock: HTTPXMock, monkeypatch):
    """After setup, mcp.instructions is set and contains relevant content."""
    monkeypatch.setenv("BW_CLIENT_ID", "CLI-test")
    monkeypatch.setenv("BW_CLIENT_SECRET", "test-secret")

    mock_token = {
        "access_token": "test-bearer-token",
        "accounts": ["12345"],
        "token_type": "bearer",
    }

    for name in [
        "messaging",
        "multi-factor-auth",
        "phone-number-lookup-v2",
        "insights",
        "end-user-management",
        "voice",
        "numbers",
        "toll-free-verification",
    ]:
        create_mock(httpx_mock, name)

    with patch("oauth.get_oauth_token", new_callable=AsyncMock) as mock_oauth:
        mock_oauth.return_value = mock_token
        from src.app import setup

        test_mcp = FastMCP(name="Integration Test")
        await setup(test_mcp)

    assert test_mcp.instructions is not None
    assert "Bandwidth MCP Server" in test_mcp.instructions
    assert "createMessage" in test_mcp.instructions


@pytest.mark.asyncio
async def test_callback_tools_available_after_setup(httpx_mock: HTTPXMock):
    """Callback and voice tools are registered after setup."""
    for name in [
        "messaging",
        "multi-factor-auth",
        "phone-number-lookup-v2",
        "insights",
        "end-user-management",
        "voice",
        "numbers",
        "toll-free-verification",
    ]:
        create_mock(httpx_mock, name)

    from src.app import setup

    test_mcp = FastMCP(name="Integration Test")
    await setup(test_mcp)

    tools = await test_mcp.get_tools()
    assert "getInboundMessages" in tools
    assert "getCallbackEvents" in tools
    assert "generateBXML" in tools
    assert "respondToCallback" in tools
    assert "setCredentials" in tools
