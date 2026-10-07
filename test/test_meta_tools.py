"""Tests for search_api and call_api meta-tools."""

import pytest
from pytest_httpx import HTTPXMock
from fastmcp import FastMCP

from tools.meta import register_meta_tools, _summarize_args
from safety import check_confirmation, confirm_token_for, validate_and_quote_path_param


def test_per_operation_confirm_tokens():
    assert confirm_token_for("numbers.CreateSite") == "CREATESITE"
    assert confirm_token_for("orderPhoneNumbers") == "ORDERPHONENUMBERS"
    assert confirm_token_for("numbers.DeleteSite") == "DELETESITE"

    # Refusal on missing or generic "CONFIRM"
    err_none = check_confirmation("numbers.CreateSite", None)
    assert err_none is not None
    assert err_none["confirm_token"] == "CREATESITE"

    err_generic = check_confirmation("numbers.CreateSite", "CONFIRM")
    assert err_generic is not None
    assert "requires confirmation with token 'CREATESITE'" in err_generic["error"]

    err_bool = check_confirmation("numbers.CreateSite", True)
    assert err_bool is not None

    # Success on matching token (case-insensitive or namespaced)
    assert check_confirmation("numbers.CreateSite", "CREATESITE") is None
    assert check_confirmation("numbers.CreateSite", "createsite") is None
    assert check_confirmation("numbers.CreateSite", "NUMBERS.CREATESITE") is None


def test_path_traversal_defense():
    with pytest.raises(ValueError, match="cannot contain"):
        validate_and_quote_path_param("siteId", "../../sippeers/123")

    with pytest.raises(ValueError, match="cannot contain"):
        validate_and_quote_path_param("orderId", "ord-1/extra")

    # Valid values are strictly URL-encoded
    assert validate_and_quote_path_param("siteId", "site 123") == "site%20123"
    assert validate_and_quote_path_param("siteId", "123") == "123"


def test_summarize_args():
    assert _summarize_args({}) == "(no arguments)"
    args = {"site_id": "123", "name": "HQ", "confirm": "CREATESITE"}
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
async def test_call_api_write_refuses_without_per_op_confirm():
    mcp = FastMCP("test")
    register_meta_tools(mcp, {})

    # No confirm token
    res = await mcp.call_tool("call_api", {
        "tool_name": "numbers.CreateSite",
        "args": {"Name": "New Site"}
    })
    sc = res.structured_content
    assert sc.get("requires_confirmation") is True
    assert sc.get("confirm_token") == "CREATESITE"
    assert "Pass confirm='CREATESITE'" in sc.get("error", "")

    # Generic confirm="CONFIRM" is rejected
    res_generic = await mcp.call_tool("call_api", {
        "tool_name": "numbers.CreateSite",
        "args": {"Name": "New Site", "confirm": "CONFIRM"}
    })
    sc_generic = res_generic.structured_content
    assert sc_generic.get("requires_confirmation") is True
    assert "requires confirmation with token 'CREATESITE'" in sc_generic.get("error", "")


@pytest.mark.asyncio
async def test_call_api_undeclared_arguments_rejected():
    mcp = FastMCP("test")
    register_meta_tools(mcp, {})

    res = await mcp.call_tool("call_api", {
        "tool_name": "numbers.ListSites",
        "args": {"bogus_argument": "dangerous_injection"}
    })
    sc = res.structured_content
    assert sc.get("status_code") == 400
    assert "Undeclared argument" in sc.get("error", "")
    assert "bogus_argument" in sc.get("error", "")
    assert "Declared parameters" in sc.get("error", "")


@pytest.mark.asyncio
async def test_call_api_path_traversal_rejected():
    config = {
        "BW_ACCESS_TOKEN": "mock-token",
        "BW_ACCOUNT_ID": "5011369",
        "BW_ACCOUNTS": ["5011369"],
    }
    mcp = FastMCP("test")
    register_meta_tools(mcp, config)

    res = await mcp.call_tool("call_api", {
        "tool_name": "numbers.DeleteSite",
        "args": {
            "siteId": "../../sippeers/123",
            "confirm": "DELETESITE",
        }
    })
    # FastMCP catches ValueError and returns an error response
    assert res.is_error or (res.structured_content and "error" in res.structured_content)


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
        headers={"Location": "https://api.bandwidth.com/api/v2/accounts/5011369/sites/42"},
    )

    res = await mcp.call_tool("call_api", {
        "tool_name": "numbers.CreateSite",
        "args": {"Name": "New Site", "confirm": "CREATESITE"}
    })
    sc = res.structured_content
    assert sc.get("status_code") == 201
    assert sc.get("id") == "42"


@pytest.mark.asyncio
async def test_call_api_destructive_fails_closed_without_elicitation(monkeypatch):
    monkeypatch.delenv("MCP_CONFIRM_FALLBACK", raising=False)
    mcp = FastMCP("test")
    register_meta_tools(mcp, {})

    res = await mcp.call_tool("call_api", {
        "tool_name": "numbers.DeleteSite",
        "args": {"siteId": "123", "confirm": "DELETESITE"}
    })
    sc = res.structured_content
    assert sc.get("elicitation_unsupported") is True
    assert "destructive" in sc.get("error", "")


@pytest.mark.asyncio
async def test_call_api_destructive_allows_with_fallback(httpx_mock: HTTPXMock, monkeypatch):
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

    res = await mcp.call_tool("call_api", {
        "tool_name": "numbers.DeleteSite",
        "args": {"siteId": "123", "confirm": "DELETESITE"}
    })
    sc = res.structured_content
    assert sc.get("status_code") == 200


@pytest.mark.asyncio
async def test_voice_update_call_bxml_sends_raw_xml(httpx_mock: HTTPXMock):
    import httpx
    captured = []
    def callback(request: httpx.Request):
        captured.append(request)
        return httpx.Response(status_code=200, text="")

    httpx_mock.add_callback(callback)
    config = {"BW_ACCESS_TOKEN": "mock-token", "BW_ACCOUNT_ID": "5011296"}
    mcp = FastMCP("test")
    register_meta_tools(mcp, config)

    res = await mcp.call_tool("call_api", {
        "tool_name": "voice.updateCallBxml",
        "args": {
            "accountId": "5011296",
            "callId": "c-12345",
            "bxml": "<Bxml><Hangup/></Bxml>",
            "confirm": "UPDATECALLBXML"
        }
    })
    sc = res.structured_content
    assert len(captured) == 1
    req = captured[0]
    assert req.headers.get("content-type") == "application/xml"
    assert req.content == b"<Bxml><Hangup/></Bxml>"


@pytest.mark.asyncio
async def test_call_api_rejects_undeclared_args():
    mcp = FastMCP("test")
    register_meta_tools(mcp, {})

    res = await mcp.call_tool("call_api", {
        "tool_name": "numbers.CreateSite",
        "args": {
            "Name": "Site 1",
            "undeclared_garbage_field": "bad",
            "confirm": "CREATESITE"
        }
    })
    sc = res.structured_content
    assert sc.get("status_code") == 400
    assert "Undeclared argument(s)" in sc.get("error", "")
    assert "undeclared_garbage_field" in sc.get("error", "")


@pytest.mark.asyncio
async def test_call_api_rejects_path_traversal():
    config = {"BW_ACCESS_TOKEN": "mock-token", "BW_ACCOUNT_ID": "5011369"}
    mcp = FastMCP("test")
    register_meta_tools(mcp, config)

    with pytest.raises(Exception) as exc_info:
        await mcp.call_tool("call_api", {
            "tool_name": "numbers.ReadSite",
            "args": {
                "siteId": "../../sippeers/123",
            }
        })
    assert "cannot contain" in str(exc_info.value)
