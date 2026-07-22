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

from xml.etree.ElementTree import Element, SubElement, fromstring, tostring

import httpx
from mcp.types import ToolAnnotations

from tools.discovery import _dashboard_get, _resolve_account
from urls import dashboard_api_base

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

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


async def _dashboard_send(
    config: dict, method: str, path: str, body: Element | None, account_id: str = ""
) -> dict:
    """Authenticated write (POST/PUT/DELETE) to /accounts/{id}/{path}.

    Body is built with ElementTree (never string interpolation) so user
    values can't inject XML. Returns parsed response plus the Location
    header's trailing id when Bandwidth returns one (order creates do)."""
    token = config.get("BW_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("Not authenticated.")
    account = _resolve_account(config, account_id)
    url = f"{dashboard_api_base()}/accounts/{account}/{path}"
    content = tostring(body, encoding="unicode") if body is not None else None
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.request(
            method,
            url,
            content=content,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/xml",
                "Accept": "application/xml",
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Bandwidth rejected the request ({resp.status_code}): {resp.text[:2000]}"
        )
    out: dict = {"httpStatus": resp.status_code}
    location = resp.headers.get("location", "")
    if location:
        out["id"] = location.rstrip("/").rsplit("/", 1)[-1]
        out["location"] = location
    if resp.text.strip():
        try:
            root = fromstring(resp.text)
            out[root.tag] = _xml_to_data(root)
        except Exception:
            out["raw"] = resp.text[:4000]
    return out


def _tn_list(parent: Element, wrapper: str, tag: str, numbers: list) -> None:
    lst = SubElement(parent, wrapper)
    for n in numbers:
        digits = "".join(ch for ch in str(n) if ch.isdigit())
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        SubElement(lst, tag).text = digits


def register_numbers_tools(mcp, config: dict) -> None:
    """Register read-only Numbers/Dashboard API tools."""

    @mcp.tool(name="listPortInOrders", annotations=_READ)
    async def list_port_in_orders(
        status: str = "", size: int = 300, account_id: str = ""
    ) -> dict:
        """List port-in (LNP) orders on the account.

        Args:
            status: Optional comma-separated Bandwidth LNP statuses to filter
                by (draft, submitted, pending_documents, exception,
                requested_supp, foc, requested_cancel, cancelled, complete).
                Pass "pending" as shorthand for every non-terminal status.
                Empty returns all orders.
            size: Max orders to return (default 300).
            account_id: Optional account to query (see listAccounts).
        """
        s = status.strip().lower()
        if s == "pending":
            s = _PENDING_LNP_STATUSES
        # page+size are REQUIRED: Bandwidth 404s /portins without them
        # (confirmed live; the 404 body even advertises the paged link).
        path = f"portins?page=1&size={int(size)}" + (f"&status={s}" if s else "")
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
    async def list_number_orders(size: int = 300, account_id: str = "") -> dict:
        """List new-number orders on the account (order history).

        Args:
            size: Max orders to return (default 300).
            account_id: Optional account to query (see listAccounts).
        """
        # page+size required here too ("Size and page parameters are required").
        return await _dashboard_json(
            config, f"orders?page=1&size={int(size)}", account_id
        )

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
    async def list_port_out_orders(
        status: str = "", size: int = 300, account_id: str = ""
    ) -> dict:
        """List port-OUT orders: numbers being ported AWAY from the account.

        Args:
            status: Optional comma-separated Bandwidth LNP statuses to filter
                by. Empty returns all port-out orders.
            size: Max orders to return (default 300).
            account_id: Optional account to query (see listAccounts).
        """
        s = status.strip().lower()
        # page+size are REQUIRED here too (same 404 quirk as /portins).
        path = f"portouts?page=1&size={int(size)}" + (f"&status={s}" if s else "")
        return await _dashboard_json(config, path, account_id)

    @mcp.tool(name="getPortOutOrder", annotations=_READ)
    async def get_port_out_order(order_id: str, account_id: str = "") -> dict:
        """Get one port-out order: status, numbers, and winning carrier info.

        Args:
            order_id: The port-out order id (from listPortOutOrders).
            account_id: Optional account to query (see listAccounts).
        """
        return await _dashboard_json(config, f"portouts/{order_id}", account_id)

    @mcp.tool(name="checkPortability", annotations=_READ)
    async def check_portability(numbers: list[str], account_id: str = "") -> dict:
        """Check whether numbers CAN port to Bandwidth, and whether they can
        port together on one order. Run this before createPortInOrder.

        Args:
            numbers: Telephone numbers to check (10-digit).
            account_id: Optional account (see listAccounts).
        """
        # lnpchecker is the one Dashboard endpoint that requires E.164
        # ("Retry request with all E.164 formatted phone numbers"); the rest
        # of the API wants bare 10-digit.
        body = Element("NumberPortabilityRequest")
        lst = SubElement(body, "TnList")
        for n in numbers:
            digits = "".join(ch for ch in str(n) if ch.isdigit())
            if len(digits) == 10:
                digits = "1" + digits
            SubElement(lst, "Tn").text = "+" + digits
        return await _dashboard_send(
            config, "POST", "lnpchecker?fullCheck=true", body, account_id
        )

    # ── carrier writes (numbers-write profile) ──────────────────────────────
    # These are LIVE carrier operations: they buy, remove, and port real
    # service. Confirm intent with the user before calling any of them.

    @mcp.tool(name="orderPhoneNumbers", annotations=_WRITE)
    async def order_phone_numbers(
        numbers: list[str],
        site_id: str,
        peer_id: str = "",
        order_name: str = "",
        account_id: str = "",
    ) -> dict:
        """ORDER (purchase) specific phone numbers onto the account. This is a
        billable carrier action. Find candidates with searchAvailableNumbers
        first, and confirm the exact numbers with the user before ordering.

        Args:
            numbers: The exact numbers to order (from searchAvailableNumbers).
            site_id: Site (sub-account) to place them on (see listSites).
            peer_id: Optional SIP peer/location (see listSipPeers).
            order_name: Optional label for the order.
            account_id: Optional account (see listAccounts).
        """
        body = Element("Order")
        if order_name:
            SubElement(body, "Name").text = order_name
        SubElement(body, "SiteId").text = site_id
        if peer_id:
            SubElement(body, "PeerId").text = peer_id
        existing = SubElement(body, "ExistingTelephoneNumberOrderType")
        _tn_list(existing, "TelephoneNumberList", "TelephoneNumber", numbers)
        return await _dashboard_send(config, "POST", "orders", body, account_id)

    @mcp.tool(name="disconnectPhoneNumbers", annotations=_DESTRUCTIVE)
    async def disconnect_phone_numbers(
        numbers: list[str], order_name: str, account_id: str = ""
    ) -> dict:
        """DISCONNECT phone numbers: removes them from service. Destructive
        and hard to undo (disconnected numbers age out of the account).
        Confirm the exact numbers with the user before calling.

        Args:
            numbers: The exact numbers to disconnect.
            order_name: A label for the disconnect order (required, shows in
                the Dashboard audit trail).
            account_id: Optional account (see listAccounts).
        """
        body = Element("DisconnectTelephoneNumberOrder")
        SubElement(body, "Name").text = order_name
        dt = SubElement(body, "DisconnectTelephoneNumberOrderType")
        _tn_list(dt, "TelephoneNumberList", "TelephoneNumber", numbers)
        return await _dashboard_send(config, "POST", "disconnects", body, account_id)

    @mcp.tool(name="createPortInOrder", annotations=_WRITE)
    async def create_port_in_order(
        billing_telephone_number: str,
        numbers: list[str],
        site_id: str,
        loa_authorizing_person: str,
        business_name: str = "",
        first_name: str = "",
        last_name: str = "",
        house_number: str = "",
        street_name: str = "",
        city: str = "",
        state_code: str = "",
        zip_code: str = "",
        requested_foc_date: str = "",
        peer_id: str = "",
        losing_carrier_account_number: str = "",
        pin: str = "",
        account_id: str = "",
    ) -> dict:
        """CREATE a port-in (LNP) order to bring numbers TO Bandwidth. A
        legally-binding carrier action against the losing carrier's account;
        run checkPortability first and confirm all details with the user.
        The signed LOA still needs uploading in the Bandwidth Dashboard
        before the order completes.

        Args:
            billing_telephone_number: The BTN on the losing carrier account.
            numbers: The numbers to port.
            site_id: Destination site (see listSites).
            loa_authorizing_person: Name of the person who signed the LOA.
            business_name: Business subscriber name (or use first/last name
                for residential).
            first_name: Residential subscriber first name.
            last_name: Residential subscriber last name.
            house_number: Service address house number.
            street_name: Service address street.
            city: Service address city.
            state_code: Service address two-letter state.
            zip_code: Service address ZIP.
            requested_foc_date: Optional requested port date (YYYY-MM-DD).
            peer_id: Optional destination SIP peer (see listSipPeers).
            losing_carrier_account_number: Account number with losing carrier.
            pin: PIN/passcode with the losing carrier, if any.
            account_id: Optional account (see listAccounts).
        """
        body = Element("LnpOrder")
        if requested_foc_date:
            SubElement(body, "RequestedFocDate").text = requested_foc_date
        btn = "".join(ch for ch in billing_telephone_number if ch.isdigit())
        if len(btn) == 11 and btn.startswith("1"):
            btn = btn[1:]
        SubElement(body, "BillingTelephoneNumber").text = btn
        subscriber = SubElement(body, "Subscriber")
        if business_name:
            SubElement(subscriber, "SubscriberType").text = "BUSINESS"
            SubElement(subscriber, "BusinessName").text = business_name
        else:
            SubElement(subscriber, "SubscriberType").text = "RESIDENTIAL"
            SubElement(subscriber, "FirstName").text = first_name
            SubElement(subscriber, "LastName").text = last_name
        addr = SubElement(subscriber, "ServiceAddress")
        SubElement(addr, "HouseNumber").text = house_number
        SubElement(addr, "StreetName").text = street_name
        SubElement(addr, "City").text = city
        SubElement(addr, "StateCode").text = state_code
        SubElement(addr, "Zip").text = zip_code
        SubElement(body, "LoaAuthorizingPerson").text = loa_authorizing_person
        _tn_list(body, "ListOfPhoneNumbers", "PhoneNumber", numbers)
        if losing_carrier_account_number:
            SubElement(body, "AccountNumber").text = losing_carrier_account_number
        if pin:
            SubElement(body, "PinNumber").text = pin
        SubElement(body, "SiteId").text = site_id
        if peer_id:
            SubElement(body, "PeerId").text = peer_id
        return await _dashboard_send(config, "POST", "portins", body, account_id)

    @mcp.tool(name="supplementPortInOrder", annotations=_WRITE)
    async def supplement_port_in_order(
        order_id: str,
        requested_foc_date: str = "",
        site_id: str = "",
        loa_authorizing_person: str = "",
        account_id: str = "",
    ) -> dict:
        """SUPP (modify) an existing port-in order: change the FOC date or
        correct details. Only pass the fields being changed.

        Args:
            order_id: The LNP order id (from listPortInOrders).
            requested_foc_date: New requested port date (YYYY-MM-DD).
            site_id: Corrected destination site.
            loa_authorizing_person: Corrected LOA signer name.
            account_id: Optional account (see listAccounts).
        """
        body = Element("LnpOrderSupp")
        if requested_foc_date:
            SubElement(body, "RequestedFocDate").text = requested_foc_date
        if site_id:
            SubElement(body, "SiteId").text = site_id
        if loa_authorizing_person:
            SubElement(body, "LoaAuthorizingPerson").text = loa_authorizing_person
        if len(body) == 0:
            raise RuntimeError("Nothing to change: pass at least one field.")
        return await _dashboard_send(
            config, "PUT", f"portins/{order_id}", body, account_id
        )

    @mcp.tool(name="cancelPortInOrder", annotations=_DESTRUCTIVE)
    async def cancel_port_in_order(order_id: str, account_id: str = "") -> dict:
        """CANCEL a port-in order (only possible before FOC). Destructive:
        the port stops and the order closes. Confirm with the user first.

        Args:
            order_id: The LNP order id (from listPortInOrders).
            account_id: Optional account (see listAccounts).
        """
        return await _dashboard_send(
            config, "DELETE", f"portins/{order_id}", None, account_id
        )
