"""MCP tools for reading callback events."""

from typing import Optional

from event_store import EventStore


async def get_inbound_messages_flow(
    event_store: EventStore,
    phone_number: Optional[str] = None,
    since: Optional[float] = None,
) -> dict:
    if phone_number:
        events = event_store.get_events(
            "messaging.inbound", key=phone_number, since=since
        )
    else:
        events = event_store.get_events("messaging.inbound", since=since)
    cleaned = [{k: v for k, v in e.items() if not k.startswith("_")} for e in events]
    return {"events": cleaned, "count": len(cleaned)}


async def get_callback_events_flow(
    event_store: EventStore,
    event_type: Optional[str] = None,
    call_id: Optional[str] = None,
    phone_number: Optional[str] = None,
    since: Optional[float] = None,
) -> dict:
    if call_id and event_type:
        events = event_store.get_events(event_type, key=call_id, since=since)
    elif phone_number and event_type:
        events = event_store.get_events(event_type, key=phone_number, since=since)
    elif event_type:
        events = event_store.get_events(event_type, since=since)
    else:
        all_events = []
        for et in [
            "messaging.inbound",
            "messaging.status",
            "voice.answer",
            "voice.gather",
            "voice.disconnect",
        ]:
            all_events.extend(event_store.get_events(et, since=since))
        all_events.sort(key=lambda e: e.get("_received_at", 0))
        events = all_events
    cleaned = [{k: v for k, v in e.items() if not k.startswith("_")} for e in events]
    return {"events": cleaned, "count": len(cleaned)}


def register_callback_tools(mcp, event_store: EventStore) -> None:
    @mcp.tool(name="getInboundMessages")
    async def get_inbound_messages(
        phone_number: Optional[str] = None,
        since: Optional[float] = None,
    ) -> dict:
        """Get recent inbound SMS/MMS messages received by your Bandwidth number.

        Args:
            phone_number: Filter by sender phone number (E.164 format).
            since: Only return events after this Unix timestamp.
        """
        return await get_inbound_messages_flow(event_store, phone_number, since)

    @mcp.tool(name="getCallbackEvents")
    async def get_callback_events(
        event_type: Optional[str] = None,
        call_id: Optional[str] = None,
        phone_number: Optional[str] = None,
        since: Optional[float] = None,
    ) -> dict:
        """Get callback events from Bandwidth webhooks.

        Filterable by event type (messaging.inbound, voice.gather, etc.),
        call ID, phone number, and timestamp.

        Args:
            event_type: Filter by event type (e.g. "messaging.inbound", "voice.gather").
            call_id: Filter voice events by call ID.
            phone_number: Filter by phone number.
            since: Only return events after this Unix timestamp.
        """
        return await get_callback_events_flow(
            event_store, event_type, call_id, phone_number, since
        )
