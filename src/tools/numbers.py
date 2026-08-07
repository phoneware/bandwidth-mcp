"""Numbers / porting tools over the Bandwidth Dashboard (Numbers) API.

The upstream server ships no Numbers-API tools ("the API is XML-based and
from_openapi sends JSON" — profiles.py), which leaves out the operations a
carrier reseller actually lives in: port-in (LNP) orders, available-number
search, new-number orders, and sites. These are hand-written tools in the same
style as tools/discovery.py: authenticated XML calls against
`{api_base}/api/v2/accounts/{accountId}/…`, returned as JSON via a generic
XML→dict conversion so Bandwidth schema drift doesn't silently drop fields.

Reads register under the `numbers` profile; the carrier WRITES (ordering,
disconnects, port-in create/supp/cancel, LOA upload) register under
`numbers-write` and only ship where the operator opts in.
"""

import base64
import re
from datetime import datetime
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
    if not xml.strip():
        # Some endpoints return an empty body for "nothing here" (e.g. a
        # port-in order with no notes).
        return {"empty": True}
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
    return _write_result(resp)


def _write_result(resp: httpx.Response) -> dict:
    """Shared handling for Dashboard writes: raise on error, else return the
    parsed body plus the Location header's trailing id (order/file creates
    return one)."""
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


def _uploaded_filename(payload: dict) -> str:
    """The stored file name out of Bandwidth's upload response body."""
    for value in payload.values():
        if isinstance(value, dict):
            for key in ("filename", "fileName", "FileName"):
                name = value.get(key)
                if isinstance(name, str) and name:
                    return name
    return ""


async def _dashboard_upload(
    config: dict, path: str, content: bytes, content_type: str, account_id: str = ""
) -> dict:
    """Authenticated binary POST to /accounts/{id}/{path}.

    Bandwidth's LNP document upload takes the raw file bytes with the
    document's own Content-Type, not multipart and not XML."""
    token = config.get("BW_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("Not authenticated.")
    account = _resolve_account(config, account_id)
    url = f"{dashboard_api_base()}/accounts/{account}/{path}"
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.post(
            url,
            content=content,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": content_type,
                "Accept": "application/xml",
            },
        )
    return _write_result(resp)


def _clean_tn(value) -> str:
    """Bare 10-digit form: strip formatting and a leading US country code."""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _e164_tn(value) -> str:
    """E.164 form (+1NXXNXXXXXX).

    Two Dashboard endpoints reject bare 10-digit numbers outright: /lnpchecker
    and /portins, both with "Retry request with all E.164 formatted phone
    numbers". Everything else wants the bare form, so this is deliberately a
    second helper rather than a change to _clean_tn."""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    return "+" + digits


def _tn_list(parent: Element, wrapper: str, tag: str, numbers: list, e164: bool = False) -> None:
    lst = SubElement(parent, wrapper)
    fmt = _e164_tn if e164 else _clean_tn
    for n in numbers:
        SubElement(lst, tag).text = fmt(n)


_ZIP_RE = re.compile(r"^\d{5}(-?\d{4})?$")

# Bandwidth's DocumentType enum on LNP file metadata.
_DOCUMENT_TYPES = ("LOA", "INVOICE", "CSR", "OTHER")

# Content types Bandwidth accepts for LNP document upload, by file extension.
_UPLOAD_TYPES = {
    "pdf": "application/pdf",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "txt": "text/plain",
}


def _port_in_problems(
    numbers: list,
    billing_telephone_number: str,
    business_name: str,
    first_name: str,
    last_name: str,
    house_number: str,
    street_name: str,
    city: str,
    state_code: str,
    zip_code: str,
    requested_foc_date: str,
    partial_port: bool,
    new_billing_telephone_number: str,
) -> list[str]:
    """Everything wrong with a proposed port-in, as fixable statements.

    Bandwidth's LNP schema makes the subscriber name and service address
    mandatory, so submitting without them just burns a live carrier write on a
    400. Catch it here and tell the agent exactly what to collect instead."""
    problems: list[str] = []

    ported = {_clean_tn(n) for n in numbers}
    if not numbers:
        problems.append("numbers: at least one telephone number to port")
    else:
        bad = [str(n) for n in numbers if len(_clean_tn(n)) != 10]
        if bad:
            problems.append(
                "numbers: must be 10-digit US numbers, got " + ", ".join(bad)
            )

    if not business_name and not (first_name and last_name):
        problems.append(
            "subscriber: business_name (business account) OR first_name + "
            "last_name (residential), exactly as it appears on the losing "
            "carrier's bill"
        )

    missing = [
        label
        for label, value in (
            ("house_number", house_number),
            ("street_name", street_name),
            ("city", city),
            ("state_code", state_code),
            ("zip_code", zip_code),
        )
        if not str(value).strip()
    ]
    if missing:
        problems.append(
            "service address (must match the losing carrier's bill): "
            + ", ".join(missing)
        )
    if state_code.strip() and not (
        len(state_code.strip()) == 2 and state_code.strip().isalpha()
    ):
        problems.append(f"state_code: two-letter state code, got {state_code!r}")
    if zip_code.strip() and not _ZIP_RE.match(zip_code.strip()):
        problems.append(f"zip_code: 5-digit ZIP or ZIP+4, got {zip_code!r}")

    if requested_foc_date.strip():
        try:
            datetime.strptime(requested_foc_date.strip(), "%Y-%m-%d")
        except ValueError:
            problems.append(
                f"requested_foc_date: YYYY-MM-DD, got {requested_foc_date!r}"
            )

    btn = _clean_tn(billing_telephone_number)
    new_btn = _clean_tn(new_billing_telephone_number)
    if partial_port and not new_btn:
        problems.append(
            "new_billing_telephone_number: required on a partial port — the TN "
            "that stays with the losing carrier and becomes the BTN on what is "
            "left of that account"
        )
    if new_btn and not partial_port:
        problems.append(
            "partial_port: pass true when new_billing_telephone_number is set"
        )
    if new_btn and new_btn in ported:
        problems.append(
            "new_billing_telephone_number must be a number staying with the "
            "losing carrier, not one of the numbers being ported"
        )
    if btn and ported and not partial_port and btn not in ported:
        problems.append(
            f"billing_telephone_number {btn} is not in numbers: a full port has "
            "to include the BTN. Either add it, or set partial_port=true and "
            "give new_billing_telephone_number."
        )

    return problems


def register_numbers_tools(mcp, config: dict) -> None:
    """Register the Numbers/Dashboard API tools (reads + carrier writes).

    Everything registers here; app.py prunes whatever the deployment's
    profile/exclude config blocks, so a numbers-only deployment never sees
    the writes."""

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

    @mcp.tool(name="listPortInLoas", annotations=_READ)
    async def list_port_in_loas(order_id: str, account_id: str = "") -> dict:
        """List the documents (LOA and friends) already uploaded to a port-in
        order. Empty means nothing is on file yet, which is why an order can
        sit in PENDING_DOCUMENTS.

        Args:
            order_id: The LNP order id.
            account_id: Optional account to query (see listAccounts).
        """
        return await _dashboard_json(config, f"portins/{order_id}/loas", account_id)

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
        # lnpchecker is one of the two LNP endpoints requiring E.164 (see
        # _e164_tn); the rest of the Dashboard API wants bare 10-digit.
        body = Element("NumberPortabilityRequest")
        _tn_list(body, "TnList", "Tn", numbers, e164=True)
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
        address_line_2: str = "",
        city: str = "",
        state_code: str = "",
        zip_code: str = "",
        requested_foc_date: str = "",
        peer_id: str = "",
        losing_carrier_account_number: str = "",
        pin: str = "",
        partial_port: bool = False,
        new_billing_telephone_number: str = "",
        customer_order_id: str = "",
        account_id: str = "",
    ) -> dict:
        """CREATE a port-in (LNP) order to bring numbers TO Bandwidth. A
        legally-binding carrier action against the losing carrier's account;
        run checkPortability first and confirm all details with the user.

        The subscriber name AND full service address are REQUIRED (Bandwidth
        rejects the order without them) and must match the losing carrier's
        bill, not wherever the numbers will end up ringing. Collect them from
        the user before calling; the tool refuses incomplete orders rather
        than firing a bad carrier write.

        Porting only SOME of the numbers on the losing account is a partial
        port: pass partial_port=true plus new_billing_telephone_number (a TN
        that stays behind). A full port must include the BTN itself.

        After the order is created, upload the signed LOA with
        uploadPortInLoa, then poll getPortInOrder.

        Args:
            billing_telephone_number: The BTN on the losing carrier account.
            numbers: The numbers to port.
            site_id: Destination site (see listSites).
            loa_authorizing_person: Name of the person who signed the LOA.
            business_name: Business subscriber name (required for a business
                port; use first_name + last_name for residential).
            first_name: Residential subscriber first name.
            last_name: Residential subscriber last name.
            house_number: Service address house number (required).
            street_name: Service address street (required).
            address_line_2: Secondary unit exactly as the losing carrier's
                record shows it (e.g. "Suite 130", "Apt 4B"). Optional, but
                send it when the CSR has one — a missing unit is a common
                address-mismatch rejection.
            city: Service address city (required).
            state_code: Service address two-letter state (required).
            zip_code: Service address ZIP or ZIP+4 (required).
            requested_foc_date: Optional requested port date (YYYY-MM-DD).
            peer_id: Optional destination SIP peer (see listSipPeers).
            losing_carrier_account_number: Account number with losing carrier.
            pin: PIN/passcode with the losing carrier, if any.
            partial_port: True when only some of the losing account's numbers
                are porting.
            new_billing_telephone_number: On a partial port, the TN that stays
                with the losing carrier and becomes its new BTN.
            customer_order_id: Optional reference of yours, echoed back on the
                order (useful for tying a port to a customer ticket).
            account_id: Optional account (see listAccounts).
        """
        problems = _port_in_problems(
            numbers,
            billing_telephone_number,
            business_name,
            first_name,
            last_name,
            house_number,
            street_name,
            city,
            state_code,
            zip_code,
            requested_foc_date,
            partial_port,
            new_billing_telephone_number,
        )
        if problems:
            raise ValueError(
                "Port-in order is incomplete, nothing was submitted to "
                "Bandwidth. Collect these from the user and call again:\n- "
                + "\n- ".join(problems)
            )

        body = Element("LnpOrder")
        if customer_order_id:
            SubElement(body, "CustomerOrderId").text = customer_order_id
        if requested_foc_date:
            SubElement(body, "RequestedFocDate").text = requested_foc_date.strip()
        # /portins rejects bare 10-digit numbers ("Retry request with all E.164
        # formatted phone numbers"), unlike the rest of the Dashboard API.
        SubElement(body, "BillingTelephoneNumber").text = _e164_tn(
            billing_telephone_number
        )
        subscriber = SubElement(body, "Subscriber")
        if business_name:
            SubElement(subscriber, "SubscriberType").text = "BUSINESS"
            SubElement(subscriber, "BusinessName").text = business_name
        else:
            SubElement(subscriber, "SubscriberType").text = "RESIDENTIAL"
            SubElement(subscriber, "FirstName").text = first_name
            SubElement(subscriber, "LastName").text = last_name
        addr = SubElement(subscriber, "ServiceAddress")
        SubElement(addr, "HouseNumber").text = house_number.strip()
        SubElement(addr, "StreetName").text = street_name.strip()
        # Bandwidth's ServiceAddress schema puts the secondary unit between the
        # street and the city; out of order it is silently dropped.
        if address_line_2.strip():
            SubElement(addr, "AddressLine2").text = address_line_2.strip()
        SubElement(addr, "City").text = city.strip()
        SubElement(addr, "StateCode").text = state_code.strip().upper()
        SubElement(addr, "Zip").text = zip_code.strip()
        SubElement(body, "LoaAuthorizingPerson").text = loa_authorizing_person
        _tn_list(body, "ListOfPhoneNumbers", "PhoneNumber", numbers, e164=True)
        if losing_carrier_account_number:
            SubElement(body, "AccountNumber").text = losing_carrier_account_number
        if pin:
            SubElement(body, "PinNumber").text = pin
        SubElement(body, "SiteId").text = site_id
        if peer_id:
            SubElement(body, "PeerId").text = peer_id
        # Partial-port pair goes last, matching Bandwidth's documented example.
        if partial_port:
            SubElement(body, "PartialPort").text = "true"
            SubElement(body, "NewBillingTelephoneNumber").text = _e164_tn(
                new_billing_telephone_number
            )
        return await _dashboard_send(config, "POST", "portins", body, account_id)

    @mcp.tool(name="uploadPortInLoa", annotations=_WRITE)
    async def upload_port_in_loa(
        order_id: str,
        file_base64: str,
        filename: str,
        document_type: str = "LOA",
        content_type: str = "",
        account_id: str = "",
    ) -> dict:
        """UPLOAD the signed LOA (or a supporting document) onto a port-in
        order. A port-in sits in PENDING_DOCUMENTS until this lands, so this
        is the step that actually gets the port moving.

        The file arrives as base64: read the signed PDF, base64-encode it, and
        pass the string. Bandwidth accepts pdf, tiff, jpeg, png, and txt.

        Args:
            order_id: The LNP order id (from createPortInOrder or
                listPortInOrders).
            file_base64: The document, base64-encoded.
            filename: Original file name, e.g. "acme-loa.pdf" (its extension
                picks the content type when content_type is not given).
            document_type: LOA (default), INVOICE, CSR, or OTHER.
            content_type: Optional MIME type override.
            account_id: Optional account (see listAccounts).
        """
        doc_type = document_type.strip().upper() or "LOA"
        if doc_type not in _DOCUMENT_TYPES:
            raise ValueError(
                f"document_type must be one of {', '.join(_DOCUMENT_TYPES)}, "
                f"got {document_type!r}"
            )
        mime = content_type.strip()
        if not mime:
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            mime = _UPLOAD_TYPES.get(ext, "")
            if not mime:
                raise ValueError(
                    f"Can't tell the file type from {filename!r}. Use a "
                    f"{'/'.join(sorted(_UPLOAD_TYPES))} extension or pass "
                    "content_type."
                )
        try:
            content = base64.b64decode(file_base64, validate=True)
        except Exception as exc:
            raise ValueError(f"file_base64 is not valid base64: {exc}") from exc
        if not content:
            raise ValueError("file_base64 decoded to an empty file.")

        uploaded = await _dashboard_upload(
            config, f"portins/{order_id}/loas", content, mime, account_id
        )
        # Bandwidth names the stored file itself; the metadata PUT is what
        # marks it as the LOA rather than an unclassified attachment.
        stored = uploaded.get("id") or _uploaded_filename(uploaded)
        if stored:
            meta = Element("FileMetaData")
            SubElement(meta, "DocumentType").text = doc_type
            try:
                uploaded["metadata"] = await _dashboard_send(
                    config,
                    "PUT",
                    f"portins/{order_id}/loas/{stored}/metadata",
                    meta,
                    account_id,
                )
            except RuntimeError as exc:
                # The file IS uploaded at this point; don't fail the tool over
                # the classification step, report it so the agent can retry.
                uploaded["metadataError"] = str(exc)
            uploaded["filename"] = stored
        return uploaded

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
