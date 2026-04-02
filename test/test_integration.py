"""Integration tests verifying the full MCP server setup."""

import os
import pytest
from fastmcp import FastMCP
from pytest_httpx import HTTPXMock
from utils import create_mock


@pytest.mark.asyncio
async def test_instructions_set_after_setup(httpx_mock: HTTPXMock, monkeypatch):
    """After setup, mcp.instructions is set and contains relevant content."""
    monkeypatch.setenv("BW_USERNAME", "test_user")
    monkeypatch.setenv("BW_PASSWORD", "test_pass")
    monkeypatch.setenv("BW_ACCOUNT_ID", "9900000")

    for name in [
        "messaging",
        "multi-factor-auth",
        "phone-number-lookup-v2",
        "insights",
        "end-user-management",
    ]:
        create_mock(httpx_mock, name)

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
