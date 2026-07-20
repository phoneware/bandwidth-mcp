"""Numbers / porting tools over the Bandwidth Dashboard (Numbers) API.

The upstream server ships no Numbers-API tools ("the API is XML-based and
from_openapi sends JSON" — profiles.py), which leaves out the operations a
carrier reseller actually lives in: port-in (LNP) orders, available-number
search, new-number orders, and sites. These are hand-written read-only tools
in the same style as tools/discovery.py: authenticated XML GETs against
`{api_base}/api/v2/accounts/{accountId}/…`, returned as JSON via a generic
XML→dict conversion so Bandwidth schema drift doesn't silently drop fields.

All tools are read-only. Number ordering/porting WRITES are deliberately not
exposed; carrier mutations stay in the Bandwidth Dashboard.
"""

from xml.etree.ElementTree import fromstring

from mcp.types import ToolAnnotations

from tools.discovery import _dashboard_get

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

# Bandwidth LNP processing statuses, for reference in tool docs:
# DRAFT, SUBMITTED, PENDING_DOCUMENTS, EXCEPTION, REQUESTED_SUPP, FOC,
# REQUESTED_CANCEL, CANCELLED, COMPLETE.
_PENDING_LNP_STATUSES = "draft,submitted,pending_documents,exception,requested_supp,foc,requested_cancel"


def _xml_to_data(el):
    """Generic XML element → JSON-safe structure.

    Text-only elements become strings; repeated sibling tags become lists;
    nested elements become dicts. Attributes are folded in under their name.
    """
    children = list(el)
    if not children:
        text = (el.text or "").strip()
        if el.attrib:
            d = dict(el.attrib)
            if text:
                d["#text"] = text
            return d
        return text
    out: dict = dict(el.attrib)
    for child in children:
        value = _xml_to_data(child)
        if child.tag in out:
            existing = out[child.tag]
            if not isinstance(existing, list):
                out[child.tag] = [existing]
            out[child.tag].append(value)
        else:
            out[child.tag] = value
    return out


async def _dashboard_json(config: dict, path: str, account_id: str = "") -> dict:
    xml = await _dashboard_get(config, path, account_id)
    root = fromstring(xml)
    return {root.tag: _xml_to_data(root)}


async def _dashboard_json_abs(config: dict, path: str) -> dict:
    """Dashboard GET for paths NOT under /accounts/{id}/ (e.g. /tns/...)."""
    import httpx

    from urls import dashboard_api_base

    token = config.get("BW_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("Not authenticated.")
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(
            f"{dashboard_api_base()}/{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/xml"},
        )
        resp.raise_for_status()
    root = fromstring(resp.text)
    return {root.tag: _xml_to_data(root)}


def register_numbers_tools(mcp, config: dict) -> None:
    """Register read-only Numbers/Dashboard API tools."""

    @mcp.tool(name="listPortInOrders", annotations=_READ)
    async def list_port_in_orders(status: str = "", account_id: str = "") -> dict:
        """List port-in (LNP) orders on the account.

        Args:
            status: Optional comma-separated Bandwidth LNP statuses to filter
                by (draft, submitted, pending_documents, exception,
                requested_supp, foc, requested_cancel, cancelled, complete).
                Pass "pending" as shorthand for every non-terminal status.
                Empty returns all orders.
            account_id: Optional account to query (see listAccounts).
        """
        s = status.strip().lower()
        if s == "pending":
            s = _PENDING_LNP_STATUSES
        path = "portins" + (f"?status={s}" if s else "")
        return await _dashboard_json(config, path, account_id)

    @mcp.tool(name="getPortInOrder", annotations=_READ)
    async def get_port_in_order(order_id: str, account_id: str = "") -> dict:
        """Get one port-in (LNP) order: status, FOC date, numbers, errors.

        Args:
            order_id: The LNP order id (from listPortInOrders).
            account_id: Optional account to query (see listAccounts).
        """
        return await _dashboard_json(config, f"portins/{order_id}", account_id)

    @mcp.tool(name="getPortInNotes", annotations=_READ)
    async def get_port_in_notes(order_id: str, account_id: str = "") -> dict:
        """Get the notes/history on a port-in (LNP) order.

        Args:
            order_id: The LNP order id.
            account_id: Optional account to query (see listAccounts).
        """
        return await _dashboard_json(config, f"portins/{order_id}/notes", account_id)

    @mcp.tool(name="searchAvailableNumbers", annotations=_READ)
    async def search_available_numbers(
        area_code: str = "",
        quantity: int = 10,
        state: str = "",
        zip_code: str = "",
        account_id: str = "",
    ) -> dict:
        """Search Bandwidth's inventory for available phone numbers.

        Read-only search; it does NOT order anything.

        Args:
            area_code: 3-digit NPA to search in.
            quantity: How many candidates to return (default 10).
            state: Two-letter state filter.
            zip_code: ZIP filter.
            account_id: Optional account to query (see listAccounts).
        """
        params = [f"quantity={int(quantity)}"]
        if area_code:
            params.append(f"areaCode={area_code}")
        if state:
            params.append(f"state={state}")
        if zip_code:
            params.append(f"zip={zip_code}")
        return await _dashboard_json(
            config, "availableNumbers?" + "&".join(params), account_id
        )

    @mcp.tool(name="listNumberOrders", annotations=_READ)
    async def list_number_orders(account_id: str = "") -> dict:
        """List new-number orders on the account (order history).

        Args:
            account_id: Optional account to query (see listAccounts).
        """
        return await _dashboard_json(config, "orders", account_id)

    @mcp.tool(name="getNumberOrder", annotations=_READ)
    async def get_number_order(order_id: str, account_id: str = "") -> dict:
        """Get one new-number order: status and the numbers it contains.

        Args:
            order_id: The order id (from listNumberOrders).
            account_id: Optional account to query (see listAccounts).
        """
        return await _dashboard_json(config, f"orders/{order_id}", account_id)

    @mcp.tool(name="listSites", annotations=_READ)
    async def list_sites(account_id: str = "") -> dict:
        """List sites (sub-accounts) on the Bandwidth account.

        Args:
            account_id: Optional account to query (see listAccounts).
        """
        return await _dashboard_json(config, "sites", account_id)

    @mcp.tool(name="listSipPeers", annotations=_READ)
    async def list_sip_peers(site_id: str, account_id: str = "") -> dict:
        """List SIP peers (locations) on a site: where its numbers route.

        Args:
            site_id: The site id (from listSites).
            account_id: Optional account to query (see listAccounts).
        """
        return await _dashboard_json(config, f"sites/{site_id}/sippeers", account_id)

    @mcp.tool(name="getPhoneNumberDetail", annotations=_READ)
    async def get_phone_number_detail(number: str) -> dict:
        """Full detail for one phone number: account, site, SIP peer, status,
        and provisioned features (e911, messaging, CNAM).

        Args:
            number: The telephone number, 10 digits (no +1).
        """
        tn = "".join(ch for ch in number if ch.isdigit())
        if len(tn) == 11 and tn.startswith("1"):
            tn = tn[1:]
        return await _dashboard_json_abs(config, f"tns/{tn}/tndetails")

    @mcp.tool(name="listPortOutOrders", annotations=_READ)
    async def list_port_out_orders(status: str = "", account_id: str = "") -> dict:
        """List port-OUT orders: numbers being ported AWAY from the account.

        Args:
            status: Optional comma-separated Bandwidth LNP statuses to filter
                by. Empty returns all port-out orders.
            account_id: Optional account to query (see listAccounts).
        """
        s = status.strip().lower()
        path = "portouts" + (f"?status={s}" if s else "")
        return await _dashboard_json(config, path, account_id)

    @mcp.tool(name="getPortOutOrder", annotations=_READ)
    async def get_port_out_order(order_id: str, account_id: str = "") -> dict:
        """Get one port-out order: status, numbers, and winning carrier info.

        Args:
            order_id: The port-out order id (from listPortOutOrders).
            account_id: Optional account to query (see listAccounts).
        """
        return await _dashboard_json(config, f"portouts/{order_id}", account_id)
