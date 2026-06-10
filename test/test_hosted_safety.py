"""Tests for hosted-mode safety defaults."""

import pytest
from fastmcp import FastMCP
from utils import tool_map


@pytest.mark.asyncio
async def test_set_credentials_registered_when_transport_unset(monkeypatch):
    monkeypatch.delenv("BW_MCP_TRANSPORT", raising=False)
    from src.tools.credentials import register_credentials_tools

    mcp = FastMCP(name="Test")
    register_credentials_tools(mcp, {})
    tools = await tool_map(mcp)
    assert "setCredentials" in tools
    assert "clearCredentials" in tools


@pytest.mark.asyncio
async def test_set_credentials_registered_when_transport_stdio(monkeypatch):
    monkeypatch.setenv("BW_MCP_TRANSPORT", "stdio")
    from src.tools.credentials import register_credentials_tools

    mcp = FastMCP(name="Test")
    register_credentials_tools(mcp, {})
    tools = await tool_map(mcp)
    assert "setCredentials" in tools
    assert "clearCredentials" in tools


@pytest.mark.asyncio
async def test_set_credentials_not_registered_for_streamable_http(monkeypatch):
    monkeypatch.setenv("BW_MCP_TRANSPORT", "streamable-http")
    from src.tools.credentials import register_credentials_tools

    mcp = FastMCP(name="Test")
    register_credentials_tools(mcp, {})
    tools = await tool_map(mcp)
    assert "setCredentials" not in tools
    assert "clearCredentials" in tools


@pytest.mark.asyncio
async def test_set_credentials_not_registered_for_sse(monkeypatch):
    monkeypatch.setenv("BW_MCP_TRANSPORT", "sse")
    from src.tools.credentials import register_credentials_tools

    mcp = FastMCP(name="Test")
    register_credentials_tools(mcp, {})
    tools = await tool_map(mcp)
    assert "setCredentials" not in tools
    assert "clearCredentials" in tools


def test_transport_config_host_default(monkeypatch):
    """Default host binds to loopback only."""
    monkeypatch.delenv("BW_MCP_HOST", raising=False)
    from src.config import get_transport_config

    cfg = get_transport_config()
    assert cfg["host"] == "127.0.0.1"


def test_transport_config_host_explicit(monkeypatch):
    """An explicit BW_MCP_HOST is honored."""
    monkeypatch.setenv("BW_MCP_HOST", "0.0.0.0")
    from src.config import get_transport_config

    cfg = get_transport_config()
    assert cfg["host"] == "0.0.0.0"
