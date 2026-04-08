"""Account discovery tools — list applications, phone numbers, sites.

The Numbers/Dashboard API is XML-based, so we can't use from_openapi.
These are hand-written tools that hit the XML endpoints directly and
return clean JSON for the agent.
"""

from xml.etree.ElementTree import fromstring

import httpx


DASHBOARD_BASE = "https://dashboard.bandwidth.com/api"


async def _dashboard_get(config: dict, path: str) -> str:
    """Make an authenticated GET to the Bandwidth Dashboard API."""
    token = config.get("BW_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("Not authenticated. Set BW_CLIENT_ID and BW_CLIENT_SECRET.")
    account_id = config.get("BW_ACCOUNT_ID")
    if not account_id:
        raise RuntimeError("No account ID. Authentication may have failed.")

    url = f"{DASHBOARD_BASE}/accounts/{account_id}/{path}"
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/xml"},
        )
        resp.raise_for_status()
        return resp.text


def _xml_text(el, tag, default=""):
    """Extract text from an XML child element."""
    child = el.find(tag)
    return child.text if child is not None and child.text else default


async def list_applications_flow(config: dict) -> dict:
    """List voice and messaging applications on the account."""
    xml = await _dashboard_get(config, "applications")
    root = fromstring(xml)

    apps = []
    for app_el in root.iter("Application"):
        apps.append({
            "applicationId": _xml_text(app_el, "ApplicationId"),
            "name": _xml_text(app_el, "AppName"),
            "serviceType": _xml_text(app_el, "ServiceType"),
            "callInitiatedCallbackUrl": _xml_text(app_el, "CallInitiatedCallbackUrl"),
            "callStatusCallbackUrl": _xml_text(app_el, "CallStatusCallbackUrl"),
        })

    return {"applications": apps, "count": len(apps)}


async def list_phone_numbers_flow(config: dict, size: int = 100) -> dict:
    """List in-service phone numbers on the account."""
    xml = await _dashboard_get(config, f"inserviceNumbers?size={size}")
    root = fromstring(xml)

    total = _xml_text(root, "TotalCount", "0")
    numbers = []
    for tn_el in root.iter("TelephoneNumber"):
        number = tn_el.text
        if number:
            # Normalize to E.164 if not already
            if not number.startswith("+"):
                number = f"+1{number}"
            numbers.append(number)

    return {"numbers": numbers, "totalCount": int(total), "returned": len(numbers)}


async def create_application_flow(
    config: dict,
    name: str,
    service_type: str = "Voice-V2",
    callback_url: str = "",
) -> dict:
    """Create a new Bandwidth application."""
    token = config.get("BW_ACCESS_TOKEN")
    account_id = config.get("BW_ACCOUNT_ID")
    if not token or not account_id:
        raise RuntimeError("Not authenticated.")

    # Use the server's base URL for callbacks if available
    base_url = config.get("BW_MCP_BASE_URL", callback_url)
    if not callback_url and base_url:
        callback_url = base_url

    # Build callback URLs based on service type
    if service_type == "Voice-V2" and callback_url:
        answer_url = f"{callback_url}/callbacks/voice/answer"
        status_url = f"{callback_url}/callbacks/voice/disconnect"
    else:
        answer_url = callback_url or "https://example.com"
        status_url = callback_url or "https://example.com"

    xml_body = f"""<Application>
        <AppName>{name}</AppName>
        <ServiceType>{service_type}</ServiceType>
        <CallInitiatedCallbackUrl>{answer_url}</CallInitiatedCallbackUrl>
        <CallStatusCallbackUrl>{status_url}</CallStatusCallbackUrl>
    </Application>"""

    url = f"{DASHBOARD_BASE}/accounts/{account_id}/applications"
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.post(
            url,
            content=xml_body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/xml",
            },
        )
        resp.raise_for_status()

    root = fromstring(resp.text)
    app_el = root.find(".//Application")
    if app_el is None:
        return {"error": "Failed to parse application response", "raw": resp.text}

    return {
        "applicationId": _xml_text(app_el, "ApplicationId"),
        "name": _xml_text(app_el, "AppName"),
        "serviceType": _xml_text(app_el, "ServiceType"),
        "callInitiatedCallbackUrl": _xml_text(app_el, "CallInitiatedCallbackUrl"),
        "callStatusCallbackUrl": _xml_text(app_el, "CallStatusCallbackUrl"),
    }


def register_discovery_tools(mcp, config: dict) -> None:
    """Register account discovery tools on the MCP server."""

    @mcp.tool(name="listApplications")
    async def list_applications() -> dict:
        """List all voice and messaging applications on your Bandwidth account.

        Returns application IDs, names, service types, and callback URLs.
        Use this to find your voice application ID for createCall.
        """
        return await list_applications_flow(config)

    @mcp.tool(name="listPhoneNumbers")
    async def list_phone_numbers(size: int = 100) -> dict:
        """List in-service phone numbers on your Bandwidth account.

        Returns phone numbers in E.164 format. Use this to find a 'from'
        number for createCall or createMessage.

        Args:
            size: Maximum numbers to return (default 100).
        """
        return await list_phone_numbers_flow(config, size)

    @mcp.tool(name="createApplication")
    async def create_application(
        name: str,
        service_type: str = "Voice-V2",
    ) -> dict:
        """Create a new Bandwidth application (voice or messaging).

        Creates the application with callback URLs automatically pointed
        at this server. Use this if listApplications returns no Voice-V2 apps.

        Args:
            name: A name for the application.
            service_type: "Voice-V2" (default) or "Messaging-V2".
        """
        return await create_application_flow(config, name, service_type)
