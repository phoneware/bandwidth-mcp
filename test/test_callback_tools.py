import pytest
from fastmcp import FastMCP
from src.event_store import EventStore
from src.tools.callbacks import register_callback_tools
from utils import tool_map


@pytest.fixture
def event_store():
    return EventStore(max_events=100, ttl_seconds=3600)


@pytest.fixture
def mcp_with_callbacks(event_store):
    mcp = FastMCP(name="Test")
    register_callback_tools(mcp, event_store)
    return mcp


@pytest.mark.asyncio
async def test_callback_tools_registered(mcp_with_callbacks):
    tools = await tool_map(mcp_with_callbacks)
    assert "getInboundMessages" in tools
    assert "getCallbackEvents" in tools


@pytest.mark.asyncio
async def test_get_inbound_messages(event_store):
    from src.tools.callbacks import get_inbound_messages_flow

    event_store.push(
        "messaging.inbound",
        "+19195551234",
        {"message": {"text": "hi", "from": "+19195551234"}},
    )
    event_store.push(
        "messaging.inbound",
        "+19195559999",
        {"message": {"text": "other", "from": "+19195559999"}},
    )
    result = await get_inbound_messages_flow(event_store)
    assert len(result["events"]) == 2
    result = await get_inbound_messages_flow(event_store, phone_number="+19195551234")
    assert len(result["events"]) == 1
    assert result["events"][0]["message"]["text"] == "hi"


@pytest.mark.asyncio
async def test_get_callback_events(event_store):
    from src.tools.callbacks import get_callback_events_flow

    event_store.push("messaging.inbound", "+19195551234", {"type": "message-received"})
    event_store.push("voice.gather", "call-1", {"type": "gather"})
    result = await get_callback_events_flow(event_store, event_type="voice.gather")
    assert len(result["events"]) == 1
    assert result["events"][0]["type"] == "gather"
