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


def _app_xml(service_type: str) -> str:
    return (
        "<ApplicationProvisioningResponse><Application>"
        "<ApplicationId>app-1</ApplicationId>"
        f"<ServiceType>{service_type}</ServiceType>"
        "<AppName>X</AppName>"
        "</Application></ApplicationProvisioningResponse>"
    )


@pytest.mark.asyncio
async def test_configure_callbacks_voice_app_sets_only_voice_fields(httpx_mock):
    """Regression: configureCallbacks with the both-types default must not send
    messaging fields to a Voice-V2 app — the API rejects that with 400/12962."""
    from src.tools.callbacks import configure_callbacks_flow

    base = "https://api.bandwidth.com/api/v2"
    url = f"{base}/accounts/123/applications/app-1"
    httpx_mock.add_response(method="GET", url=url, text=_app_xml("Voice-V2"))
    httpx_mock.add_response(method="PUT", url=url, text="<ok/>", status_code=200)

    cfg = {"BW_ACCESS_TOKEN": "t", "BW_ACCOUNT_ID": "123"}
    res = await configure_callbacks_flow(
        cfg, "app-1", "https://srv.example.com", types=["voice", "messaging"]
    )

    assert res["status"] == "configured"
    assert set(res["callbacks"]) == {"callInitiatedCallbackUrl", "callStatusCallbackUrl"}
    put_body = [r for r in httpx_mock.get_requests() if r.method == "PUT"][0].content.decode()
    # Messaging tags must be absent (angle-bracketed to avoid matching the
    # voice tags, which contain "CallbackUrl" as a substring).
    assert "<CallbackUrl>" not in put_body
    assert "<StatusCallbackUrl>" not in put_body
    assert "<CallInitiatedCallbackUrl>" in put_body


@pytest.mark.asyncio
async def test_configure_callbacks_messaging_app_sets_only_messaging_fields(httpx_mock):
    from src.tools.callbacks import configure_callbacks_flow

    base = "https://api.bandwidth.com/api/v2"
    url = f"{base}/accounts/123/applications/app-1"
    httpx_mock.add_response(method="GET", url=url, text=_app_xml("Messaging-V2"))
    httpx_mock.add_response(method="PUT", url=url, text="<ok/>", status_code=200)

    cfg = {"BW_ACCESS_TOKEN": "t", "BW_ACCOUNT_ID": "123"}
    res = await configure_callbacks_flow(
        cfg, "app-1", "https://srv.example.com", types=["voice", "messaging"]
    )

    assert res["status"] == "configured"
    assert set(res["callbacks"]) == {"callbackUrl", "statusCallbackUrl"}
    put_body = [r for r in httpx_mock.get_requests() if r.method == "PUT"][0].content.decode()
    assert "<CallInitiatedCallbackUrl>" not in put_body
    assert "<CallStatusCallbackUrl>" not in put_body
    assert "<CallbackUrl>" in put_body


@pytest.mark.asyncio
async def test_configure_callbacks_unknown_service_type_errors(httpx_mock):
    from src.tools.callbacks import configure_callbacks_flow

    base = "https://api.bandwidth.com/api/v2"
    url = f"{base}/accounts/123/applications/app-1"
    httpx_mock.add_response(method="GET", url=url, text=_app_xml("Mystery-V9"))

    cfg = {"BW_ACCESS_TOKEN": "t", "BW_ACCOUNT_ID": "123"}
    res = await configure_callbacks_flow(cfg, "app-1", "https://srv.example.com")
    assert "error" in res
    assert "Mystery-V9" in res["error"]
