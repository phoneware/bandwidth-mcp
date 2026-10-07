"""Tests for search_api and call_api meta-tools."""

import pytest
from pytest_httpx import HTTPXMock
from fastmcp import FastMCP

from tools.meta import register_meta_tools, _is_confirmed, _summarize_args
from promotion import set_usage_store_for_tests, InMemoryUsageStore


def test_is_confirmed():
    assert not _is_confirmed({})
    assert not _is_confirmed({"confirm": "no"})
    assert not _is_confirmed({"confirm": False})
    assert _is_confirmed({"confirm": "CONFIRM"})
    assert _is_confirmed({"confirm": "confirm"})
    assert _is_confirmed({"confirm": True})
    assert _is_confirmed({"confirm_token": "token-123"})


def test_summarize_args():
    assert _summarize_args({}) == "(no arguments)"
    args = {"site_id": "123", "name": "HQ", "confirm": "CONFIRM"}
    summary = _summarize_args(args)
    assert "site_id=123" in summary
    assert "name=HQ" in summary
    assert "confirm" not in summary


@pytest.mark.asyncio
async def test_search_api_tool():
    mcp = FastMCP("test")
    register_meta_tools(mcp, {})

    res = await mcp.call_tool("search_api", {"query": "sites", "limit": 5})
    sc = res.structured_content
    assert sc.get("total_matches") > 0
    matches = sc.get("matches", [])
    assert len(matches) <= 5
    names = [m["name"] for m in matches]
    assert "numbers.ListSites" in names


@pytest.mark.asyncio
async def test_call_api_unknown_tool():
    mcp = FastMCP("test")
    register_meta_tools(mcp, {})

    res = await mcp.call_tool("call_api", {"tool_name": "nonexistent.tool", "args": {}})
    sc = res.structured_content
    assert sc.get("status_code") == 404
    assert "not registered" in sc.get("error", "")


@pytest.mark.asyncio
async def test_call_api_write_refuses_without_confirm():
    mcp = FastMCP("test")
    register_meta_tools(mcp, {})

    res = await mcp.call_tool(
        "call_api", {"tool_name": "numbers.CreateSite", "args": {"Name": "New Site"}}
    )
    sc = res.structured_content
    assert sc.get("requires_confirmation") is True
    assert "requires confirmation" in sc.get("error", "")


@pytest.mark.asyncio
async def test_call_api_write_proceeds_with_confirm(httpx_mock: HTTPXMock):
    config = {
        "BW_ACCESS_TOKEN": "mock-token",
        "BW_ACCOUNT_ID": "5011369",
        "BW_ACCOUNTS": ["5011369"],
    }
    mcp = FastMCP("test")
    register_meta_tools(mcp, config)

    httpx_mock.add_response(
        method="POST",
        url="https://api.bandwidth.com/api/v2/accounts/5011369/sites",
        status_code=201,
        headers={
            "Location": "https://api.bandwidth.com/api/v2/accounts/5011369/sites/42"
        },
    )

    res = await mcp.call_tool(
        "call_api",
        {
            "tool_name": "numbers.CreateSite",
            "args": {"Name": "New Site", "confirm": "CONFIRM"},
        },
    )
    sc = res.structured_content
    assert sc.get("status_code") == 201
    assert sc.get("id") == "42"


@pytest.mark.asyncio
async def test_call_api_destructive_fails_closed_without_elicitation(monkeypatch):
    monkeypatch.delenv("MCP_CONFIRM_FALLBACK", raising=False)
    mcp = FastMCP("test")
    register_meta_tools(mcp, {})

    res = await mcp.call_tool(
        "call_api",
        {
            "tool_name": "numbers.DeleteSite",
            "args": {"siteId": "123", "confirm": "CONFIRM"},
        },
    )
    sc = res.structured_content
    assert sc.get("elicitation_unsupported") is True
    assert "destructive" in sc.get("error", "")


@pytest.mark.asyncio
async def test_call_api_destructive_allows_with_fallback(
    httpx_mock: HTTPXMock, monkeypatch
):
    monkeypatch.setenv("MCP_CONFIRM_FALLBACK", "allow")
    config = {
        "BW_ACCESS_TOKEN": "mock-token",
        "BW_ACCOUNT_ID": "5011369",
        "BW_ACCOUNTS": ["5011369"],
    }
    mcp = FastMCP("test")
    register_meta_tools(mcp, config)

    httpx_mock.add_response(
        method="DELETE",
        url="https://api.bandwidth.com/api/v2/accounts/5011369/sites/123",
        status_code=200,
    )

    res = await mcp.call_tool(
        "call_api",
        {
            "tool_name": "numbers.DeleteSite",
            "args": {"siteId": "123", "confirm": "CONFIRM"},
        },
    )
    sc = res.structured_content
    assert sc.get("status_code") == 200
