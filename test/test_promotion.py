"""Tests for per-user tool promotion."""

import time
import pytest
from fastmcp import FastMCP

from promotion import (
    InMemoryUsageStore,
    set_usage_store_for_tests,
    get_promoted_tool_names,
    record_call_api_invocation,
    promote_tool_on_server,
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
