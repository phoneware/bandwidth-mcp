"""Tests for per-user tool promotion."""

import time
import pytest
from pytest_httpx import HTTPXMock
from fastmcp import FastMCP

from promotion import (
    InMemoryUsageStore,
    set_usage_store_for_tests,
    get_promoted_tool_names,
    record_call_api_invocation,
    promote_tool_on_server,
    setup_promotions,
    current_user_var,
    get_usage_store,
    UsageRecord,
)
from utils import tool_map

@pytest.mark.asyncio
async def test_in_memory_usage_store():
    store = InMemoryUsageStore()
    user = "alice@example.com"

    r1 = await store.record_call(user, "numbers.GetAccount")
    assert r1.count == 1

    r2 = await store.record_call(user, "numbers.GetAccount")
    assert r2.count == 2

    usage = await store.get_user_usage(user)
    assert "numbers.GetAccount" in usage
    assert usage["numbers.GetAccount"].count == 2


@pytest.mark.asyncio
async def test_promotion_threshold(monkeypatch):
    monkeypatch.setenv("MCP_PROMOTE_THRESHOLD", "3")
    monkeypatch.setenv("MCP_PROMOTE_WINDOW_DAYS", "14")
    store = InMemoryUsageStore()
    set_usage_store_for_tests(store)

    user = "bob@example.com"
    # Call 1: not promoted
    res1 = await record_call_api_invocation(user, "numbers.GetAccount")
    assert not res1["promoted"]
    assert res1["count"] == 1
    promoted = await get_promoted_tool_names(user)
    assert "numbers.GetAccount" not in promoted

    # Call 2: not promoted
    res2 = await record_call_api_invocation(user, "numbers.GetAccount")
    assert not res2["promoted"]
    assert res2["count"] == 2
    promoted = await get_promoted_tool_names(user)
    assert "numbers.GetAccount" not in promoted

    # Call 3: crossing threshold -> promoted!
    res3 = await record_call_api_invocation(user, "numbers.GetAccount")
    assert res3["promoted"]
    assert res3["count"] == 3
    promoted = await get_promoted_tool_names(user)
    assert "numbers.GetAccount" in promoted


@pytest.mark.asyncio
async def test_promotion_window_decay(monkeypatch):
    monkeypatch.setenv("MCP_PROMOTE_THRESHOLD", "2")
    monkeypatch.setenv("MCP_PROMOTE_WINDOW_DAYS", "1")
    store = InMemoryUsageStore()
    set_usage_store_for_tests(store)

    user = "carol@example.com"
    # Record 2 calls in the past (2 days ago)
    past_time = time.time() - (2 * 86400)
    store._data[user] = {
        "numbers.GetAccount": InMemoryUsageStore.__dict__["record_call"]  # placeholder
    }
    from promotion import UsageRecord

    store._data[user]["numbers.GetAccount"] = UsageRecord(count=5, last_used=past_time)

    # Decayed outside window -> not promoted
    promoted = await get_promoted_tool_names(user)
    assert "numbers.GetAccount" not in promoted


@pytest.mark.asyncio
async def test_promotion_disabled(monkeypatch):
    monkeypatch.setenv("MCP_DISABLE_PROMOTION", "true")
    store = InMemoryUsageStore()
    set_usage_store_for_tests(store)

    user = "dave@example.com"
    for _ in range(5):
        await record_call_api_invocation(user, "numbers.GetAccount")

    promoted = await get_promoted_tool_names(user)
    assert len(promoted) == 0


@pytest.mark.asyncio
async def test_promote_tool_on_server():
    mcp = FastMCP("test")
    config = {"BW_ACCESS_TOKEN": "token"}
    success = promote_tool_on_server(mcp, "numbers.GetAccount", config)
    assert success is True

    tools = await tool_map(mcp)
    assert "numbers.GetAccount" in tools


@pytest.mark.asyncio
async def test_promotions_scoped_per_user(monkeypatch):
    monkeypatch.setenv("MCP_PROMOTE_THRESHOLD", "3")
    monkeypatch.setenv("MCP_PROMOTE_WINDOW_DAYS", "14")
    store = InMemoryUsageStore()
    set_usage_store_for_tests(store)

    mcp = FastMCP("test-server")
    config = {"BW_ACCESS_TOKEN": "token", "BW_ACCOUNT_ID": "5011369"}
    setup_promotions(mcp, config)

    # Alice invokes numbers.GetAccount 3 times
    alice = "alice@phoneware.us"
    bob = "bob@phoneware.us"
    for _ in range(3):
        await record_call_api_invocation(alice, "numbers.GetAccount", mcp_instance=mcp, config=config)

    # Check Alice sees the promoted tool
    tok_alice = current_user_var.set(alice)
    try:
        alice_tools = [t.name for t in await mcp._list_tools()]
        assert "numbers.GetAccount" in alice_tools
        alice_tool = await mcp._get_tool("numbers.GetAccount")
        assert alice_tool is not None
    finally:
        current_user_var.reset(tok_alice)

    # Check Bob does NOT see Alice's promoted tool
    tok_bob = current_user_var.set(bob)
    try:
        bob_tools = [t.name for t in await mcp._list_tools()]
        assert "numbers.GetAccount" not in bob_tools
        bob_tool = await mcp._get_tool("numbers.GetAccount")
        assert bob_tool is None
    finally:
        current_user_var.reset(tok_bob)


@pytest.mark.asyncio
async def test_firestore_selected_on_cloud_run(monkeypatch):
    set_usage_store_for_tests(None)
    monkeypatch.setenv("K_SERVICE", "bandwidth-mcp")
    monkeypatch.delenv("MCP_PERSISTENCE", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    import sys
    from unittest.mock import MagicMock
    mock_firestore = MagicMock()
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", mock_firestore)

    from promotion import FirestoreUsageStore
    store = get_usage_store()
    assert isinstance(store, FirestoreUsageStore)
    set_usage_store_for_tests(None)


@pytest.mark.asyncio
async def test_promotions_reloaded_after_server_restart(monkeypatch):
    monkeypatch.setenv("MCP_PROMOTE_THRESHOLD", "3")
    monkeypatch.setenv("MCP_PROMOTE_WINDOW_DAYS", "14")
    store = InMemoryUsageStore()
    set_usage_store_for_tests(store)

    user = "alice@phoneware.us"
    # Simulate pre-existing 4 calls stored in Firestore / persistent store
    now = time.time()
    store._data[user] = {
        "numbers.GetAccount": UsageRecord(count=4, last_used=now, timestamps=[now] * 4)
    }

    # Server starts up fresh (no tools dynamically registered yet)
    mcp = FastMCP("fresh-server")
    config = {"BW_ACCESS_TOKEN": "token", "BW_ACCOUNT_ID": "5011369"}
    setup_promotions(mcp, config)

    tok = current_user_var.set(user)
    try:
        # tools/list should reload Alice's pre-existing promotion
        tools = [t.name for t in await mcp._list_tools()]
        assert "numbers.GetAccount" in tools

        # Direct tool retrieval should also reload it
        tool = await mcp._get_tool("numbers.GetAccount")
        assert tool is not None
    finally:
        current_user_var.reset(tok)


@pytest.mark.asyncio
async def test_promoted_destructive_tool_forwards_mcp_context(httpx_mock: HTTPXMock, monkeypatch):
    from fastmcp import Context
    from unittest.mock import AsyncMock, MagicMock

    mcp = FastMCP("test-destructive")
    config = {
        "BW_ACCESS_TOKEN": "mock-token",
        "BW_ACCOUNT_ID": "5011369",
        "BW_ACCOUNTS": ["5011369"],
    }
    setup_promotions(mcp, config)
    store = InMemoryUsageStore()
    set_usage_store_for_tests(store)

    user = "alice@phoneware.us"
    tok = current_user_var.set(user)
    now = time.time()
    store._data[user] = {
        "numbers.DeleteSite": UsageRecord(count=3, last_used=now, timestamps=[now] * 3)
    }
    promote_tool_on_server(mcp, "numbers.DeleteSite", config)

    # Mock Context with elicit returning True
    mock_ctx = AsyncMock(spec=Context)
    elicit_result = MagicMock()
    elicit_result.value = True
    mock_ctx.elicit = AsyncMock(return_value=elicit_result)

    # When invoked through mcp with Context, ctx reaches the elicitation gate
    tool = await mcp._get_tool("numbers.DeleteSite")
    assert tool is not None

    # Call the tool handler directly passing mock_ctx
    import inspect
    sig = inspect.signature(tool.fn)
    assert "ctx" in sig.parameters

    httpx_mock.add_response(
        method="DELETE",
        url="https://api.bandwidth.com/api/v2/accounts/5011369/sites/123",
        status_code=200,
        text="",
    )
    result = await tool.fn(args={"siteId": "123", "confirm": "DELETESITE"}, ctx=mock_ctx)
    assert result.get("status_code") == 200
    assert not result.get("elicitation_unsupported")
    assert mock_ctx.elicit.await_count == 1
    current_user_var.reset(tok)

@pytest.mark.asyncio
async def test_usage_window_expires_old_calls(monkeypatch):
    monkeypatch.setenv("MCP_PROMOTE_THRESHOLD", "3")
    monkeypatch.setenv("MCP_PROMOTE_WINDOW_DAYS", "14")
    store = InMemoryUsageStore()
    set_usage_store_for_tests(store)

    user = "alice@phoneware.us"
    old_time = time.time() - (20 * 86400)  # 20 days ago (outside 14-day window)
    store._data[user] = {
        "numbers.GetAccount": UsageRecord(count=2, last_used=old_time, timestamps=[old_time, old_time])
    }

    # Today Alice makes 1 call
    res = await record_call_api_invocation(user, "numbers.GetAccount")
    # Old calls should have expired, so count is 1 (not 3), and NOT promoted
    assert res["count"] == 1
    assert not res["promoted"]
