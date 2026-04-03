"""MCP tools for reading callback events and configuring webhooks."""

from typing import Optional

import httpx

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


async def configure_callbacks_flow(
    config: dict,
    application_id: str,
    base_url: str,
    types: Optional[list[str]] = None,
) -> dict:
    """Update a Bandwidth application's callback URLs via the Dashboard XML API."""
    if types is None:
        types = ["messaging", "voice"]

    token = config.get("BW_ACCESS_TOKEN")
    account_id = config.get("BW_ACCOUNT_ID")
    if not token or not account_id:
        return {"error": "Not authenticated. Call setCredentials first."}

    # Build callback URLs
    callbacks: dict = {}
    if "voice" in types:
        callbacks["callInitiatedCallbackUrl"] = f"{base_url}/callbacks/voice/answer"
        callbacks["callStatusCallbackUrl"] = f"{base_url}/callbacks/voice/disconnect"
    if "messaging" in types:
        callbacks["callbackUrl"] = f"{base_url}/callbacks/messaging/inbound"
        callbacks["statusCallbackUrl"] = f"{base_url}/callbacks/messaging/status"

    # First GET the current app to preserve its name and service type
    api_url = f"https://dashboard.bandwidth.com/api/accounts/{account_id}/applications/{application_id}"

    async with httpx.AsyncClient(follow_redirects=True) as client:
        get_resp = await client.get(
            api_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/xml"},
        )

    # Extract current name and service type from the XML
    from xml.etree.ElementTree import fromstring

    app_xml = fromstring(get_resp.text)
    app_el = app_xml.find(".//Application") or app_xml
    app_name = ""
    service_type = ""
    for child in app_el:
        if child.tag == "AppName":
            app_name = child.text or ""
        if child.tag == "ServiceType":
            service_type = child.text or ""

    # Build the full XML with required fields + updated callbacks
    xml_parts = [
        f"<ServiceType>{service_type}</ServiceType>",
        f"<AppName>{app_name}</AppName>",
    ]
    # XML tags match the Bandwidth Dashboard API naming (PascalCase)
    tag_map = {
        "callInitiatedCallbackUrl": "CallInitiatedCallbackUrl",
        "callStatusCallbackUrl": "CallStatusCallbackUrl",
        "callbackUrl": "CallbackUrl",
        "statusCallbackUrl": "StatusCallbackUrl",
    }
    for k, v in callbacks.items():
        tag = tag_map.get(k, k)
        xml_parts.append(f"<{tag}>{v}</{tag}>")

    xml_body = f"<Application>{''.join(xml_parts)}</Application>"

    async with httpx.AsyncClient(follow_redirects=True) as client:
        response = await client.put(
            api_url,
            content=xml_body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/xml",
            },
        )

    if response.status_code >= 400:
        return {
            "error": f"Failed to update application ({response.status_code})",
            "details": response.text,
        }

    return {
        "status": "configured",
        "application_id": application_id,
        "base_url": base_url,
        "types": types,
        "callbacks": callbacks,
    }


def register_callback_tools(mcp, event_store: EventStore, config: dict = None) -> None:
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

    if config is not None:

        @mcp.tool(name="configureCallbacks")
        async def configure_callbacks(
            application_id: str,
            base_url: str,
            types: Optional[list[str]] = None,
        ) -> dict:
            """Configure a Bandwidth application's callback URLs to point at this server.

            Sets the application's webhook URLs so Bandwidth sends inbound messages
            and voice events to this MCP server. One call and webhooks are wired.

            Args:
                application_id: The Bandwidth application ID to configure.
                base_url: The public URL of this server (e.g. https://your-server.ngrok.io).
                types: Which callback types to register. Default: ["messaging", "voice"].
            """
            return await configure_callbacks_flow(config, application_id, base_url, types)
