"""Numbers / porting tools over the Bandwidth Dashboard (Numbers) API.

The upstream server ships no Numbers-API tools ("the API is XML-based and
from_openapi sends JSON", profiles.py), which leaves out the operations a
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
from datetime import datetime, time
from functools import lru_cache
from xml.etree.ElementTree import Element, SubElement, fromstring, tostring
from zoneinfo import ZoneInfo
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


@lru_cache(maxsize=1)
def _eastern() -> ZoneInfo:
    """America/New_York, resolved on first use.

    Every activation window Bandwidth publishes is stated in Eastern, so the
    conversion belongs here rather than in a caller computing its own DST
    offset. Resolved lazily and cached: a container missing tzdata should cost
    the one tool that schedules a time, not every tool in this module at
    import."""
    return ZoneInfo("America/New_York")


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

def _sanitize_customer_order_id(value: str) -> str:
    """Sanitize customer_order_id to Bandwidth's accepted charset (error 7318).

    Alphanumerics, dashes, and spaces only; max 255 characters. Dotted ticket
    numbers (e.g. T20260806.0030) convert dots to dashes (T20260806-0030).
    Runs of whitespace are collapsed. Returns an empty string if nothing usable
    remains.
    """
    if not value:
        return ""
    s = re.sub(r"(?<=[a-zA-Z0-9])\.(?=[a-zA-Z0-9])", "-", value)
    s = re.sub(r"[^a-zA-Z0-9 -]", "", s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    return s[:255]


def _extract_sites(payload: dict) -> list[dict]:
    """Extract the site list from Bandwidth's /sites response."""
    sites_wrapper = payload
    if isinstance(sites_wrapper, dict) and "SitesResponse" in sites_wrapper:
        sites_wrapper = sites_wrapper["SitesResponse"]
    if isinstance(sites_wrapper, dict) and "Sites" in sites_wrapper:
        sites_wrapper = sites_wrapper["Sites"]
    if isinstance(sites_wrapper, dict) and "Site" in sites_wrapper:
        site_val = sites_wrapper["Site"]
        if isinstance(site_val, list):
            return site_val
        if isinstance(site_val, dict):
            return [site_val]
    return []


async def _resolve_site(
    config: dict, site_id: str, site_name: str, account_id: str = ""
) -> str:
    """Resolve site_id and site_name against the account's sites.

    Exactly one case-insensitive site_name match proceeds. Zero matches or
    several fail with a ValueError listing candidate names. Passing both
    site_id and site_name is an error unless they agree. Passing neither is an
    error.
    """
    sid = str(site_id).strip()
    sname = str(site_name).strip()
    if not sid and not sname:
        raise ValueError(
            "site_id or site_name is required: provide a destination site by ID or by name"
        )
    if sname:
        sites_payload = await _dashboard_json(config, "sites", account_id)
        sites = _extract_sites(sites_payload)
        candidate_names = [str(s.get("Name", "")) for s in sites if s.get("Name")]
        target = sname.lower()
        matches = [
            s for s in sites if str(s.get("Name", "")).strip().lower() == target
        ]
        if not matches:
            available = (
                ", ".join(f"{n!r}" for n in candidate_names)
                if candidate_names
                else "(none)"
            )
            raise ValueError(
                f"Unknown site_name {site_name!r}. Available sites: {available}"
            )
        if len(matches) > 1:
            matching_desc = ", ".join(
                f"{m.get('Name')!r} (id: {m.get('Id')})" for m in matches
            )
            raise ValueError(
                f"Ambiguous site_name {site_name!r} matches multiple sites: {matching_desc}"
            )
        resolved_id = str(matches[0].get("Id", "")).strip()
        if sid and sid != resolved_id:
            raise ValueError(
                f"site_id {site_id!r} and site_name {site_name!r} disagree: "
                f"{site_name!r} has site_id {resolved_id!r}"
            )
        return resolved_id
    return sid


def _format_foc_date_time(foc_date: str, foc_time: str) -> tuple[str, bool]:
    """Format requested FOC date and optional time.

    Returns (formatted_date_str, is_triggered).
    If foc_time is given, localizes to Eastern time (America/New_York) and
    formats as %Y-%m-%dT%H:%M:00%z with Triggered=True.
    If only foc_date is given, returns the bare date and Triggered=False.
    """
    date_str = foc_date.strip()
    time_str = foc_time.strip()
    if not date_str:
        return "", False
    if not time_str:
        return date_str, False
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(
        tzinfo=_eastern()
    )
    return dt.strftime("%Y-%m-%dT%H:%M:00%z"), True


async def _upload_port_in_document(
    config: dict,
    order_id: str,
    file_base64: str,
    filename: str,
    document_type: str = "LOA",
    content_type: str = "",
    account_id: str = "",
) -> dict:
    """Upload an LNP document (LOA, invoice, CSR) onto a port-in order."""
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
            uploaded["metadataError"] = str(exc)
        uploaded["filename"] = stored
    return uploaded

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
    requested_foc_time: str = "",
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

    if requested_foc_time.strip():
        if not requested_foc_date.strip():
            problems.append(
                "requested_foc_time: requires requested_foc_date (YYYY-MM-DD) to also be set"
            )
        try:
            t = datetime.strptime(requested_foc_time.strip(), "%H:%M").time()
            if t < time(5, 0) or t > time(22, 0):
                problems.append(
                    f"requested_foc_time: {requested_foc_time.strip()!r} is outside "
                    "Bandwidth's activation windows (automated off-net 06:00-22:00 ET, "
                    "automated on-net and internal 05:00-22:00 ET)"
                )
        except ValueError:
            problems.append(
                f"requested_foc_time: 24h HH:MM in Eastern time, got {requested_foc_time!r}"
            )
    btn = _clean_tn(billing_telephone_number)
    new_btn = _clean_tn(new_billing_telephone_number)
    # A replacement BTN is only meaningful when the BTN itself is porting: the
    # remaining account has lost its billing number and needs another. When the
    # BTN stays, it simply remains the BTN, and Bandwidth rejects being told
    # otherwise ("NewBillingTelephoneNumber cannot be the same as the
    # BillingTelephoneNumber", error 7497).
    if partial_port and btn in ported and not new_btn:
        problems.append(
            "new_billing_telephone_number: required when the BTN itself is "
            "porting: the TN that stays with the losing carrier and becomes "
            "the BTN on what is left of that account"
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
    if new_btn and new_btn == btn:
        problems.append(
            "new_billing_telephone_number must differ from "
            "billing_telephone_number: the BTN is not porting, so it stays the "
            "BTN. Leave new_billing_telephone_number empty."
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
        loa_authorizing_person: str,
        site_id: str = "",
        site_name: str = "",
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
        requested_foc_time: str = "",
        peer_id: str = "",
        losing_carrier_account_number: str = "",
        pin: str = "",
        partial_port: bool = False,
        new_billing_telephone_number: str = "",
        customer_order_id: str = "",
        loa_file_base64: str = "",
        loa_filename: str = "",
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

        Activation time: pass requested_foc_time in 24h Eastern time (e.g.
        "20:00" for 8:00 PM ET) alongside requested_foc_date to schedule an
        activation time. The tool converts to the correct Eastern offset
        automatically and sets Triggered=true. Valid windows are 05:00 to
        22:00 ET (on-net/internal) and 06:00 to 22:00 ET (off-net).
        Manual port types (manual off-net, manual toll free, phase 1 automated
        toll free, project ports) always activate at 11:30 AM ET and ignore
        both fields: run checkPortability first to learn the port type before
        promising a customer an activation time.

        Destination site: specify site_id or site_name. site_name is resolved
        case-insensitively against the account's sites (see listSites).

        LOA upload: you may pass loa_file_base64 (and optional loa_filename)
        to attach the LOA document in the same call. Otherwise, upload it
        afterward with uploadPortInLoa, then poll getPortInOrder.

        Args:
            billing_telephone_number: The BTN on the losing carrier account.
            numbers: The numbers to port.
            loa_authorizing_person: Name of the person who signed the LOA.
            site_id: Destination site ID (see listSites). Optional if site_name is given.
            site_name: Destination site name (case-insensitive, resolved against
                the account's sites). Optional if site_id is given.
            business_name: Business subscriber name (required for a business
                port; use first_name + last_name for residential).
            first_name: Residential subscriber first name.
            last_name: Residential subscriber last name.
            house_number: Service address house number (required).
            street_name: Service address street (required).
            address_line_2: Secondary unit exactly as the losing carrier's
                record shows it (e.g. "Suite 130", "Apt 4B"). Optional, but
                send it when the CSR has one: a missing unit is a common
                address-mismatch rejection.
            city: Service address city (required).
            state_code: Service address two-letter state (required).
            zip_code: Service address ZIP or ZIP+4 (required).
            requested_foc_date: Optional requested port date (YYYY-MM-DD).
            requested_foc_time: Optional requested activation time in 24h
                Eastern time (e.g. "20:00" for 8:00 PM ET). Requires
                requested_foc_date. Sets Triggered=true.
            peer_id: Optional destination SIP peer (see listSipPeers).
            losing_carrier_account_number: Account number with the losing
                carrier, from their CSR or bill. Optional to Bandwidth but
                required by most losing carriers: without it the order is
                accepted here and rejected there, days later. Collect it.
            pin: PIN/passcode with the losing carrier. Same story as the
                account number: get it if the carrier issues one.
            partial_port: True when only some of the losing account's numbers
                are porting.
            new_billing_telephone_number: Only when the BTN is itself porting:
                the TN that stays with the losing carrier and becomes its new
                BTN. Leave empty when the BTN is not in `numbers`: it stays
                the BTN, and passing it here is rejected.
            customer_order_id: Optional reference of yours, echoed back on the
                order (useful for tying a port to a customer ticket).
                Alphanumeric, dashes and spaces only, max 255. Defaults to
                subscriber name plus porting number for single-number ports,
                or subscriber name alone for several.
            loa_file_base64: Optional base64-encoded LOA document (PDF, TIFF,
                PNG, JPEG) to attach in the same call.
            loa_filename: Optional filename for the LOA (e.g. "acme-loa.pdf").
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
            requested_foc_time,
        )
        if problems:
            raise ValueError(
                "Port-in order is incomplete, nothing was submitted to "
                "Bandwidth. Collect these from the user and call again:\n- "
                + "\n- ".join(problems)
            )

        resolved_site_id = await _resolve_site(
            config, site_id, site_name, account_id
        )

        if customer_order_id.strip():
            final_order_id = _sanitize_customer_order_id(customer_order_id)
        else:
            sub_name = (
                business_name.strip()
                if business_name.strip()
                else f"{first_name.strip()} {last_name.strip()}".strip()
            )
            if len(numbers) == 1:
                raw_default = f"{sub_name} {_clean_tn(numbers[0])}".strip()
            else:
                raw_default = sub_name
            final_order_id = _sanitize_customer_order_id(raw_default)

        body = Element("LnpOrder")
        if final_order_id:
            SubElement(body, "CustomerOrderId").text = final_order_id
        if requested_foc_date.strip():
            foc_date_formatted, is_triggered = _format_foc_date_time(
                requested_foc_date, requested_foc_time
            )
            SubElement(body, "RequestedFocDate").text = foc_date_formatted
            if is_triggered:
                SubElement(body, "Triggered").text = "true"
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
        # The losing carrier's account number and PIN live inside <WirelessInfo>,
        # whatever the name suggests: it is where Bandwidth keeps them for
        # wireline ports too (LosingCarrierIsWireless=false orders come back with
        # exactly this shape). As top-level children of LnpOrder they are
        # silently dropped, the order is accepted looking complete, and the
        # losing carrier rejects it days later for a missing account number.
        if losing_carrier_account_number or pin:
            wireless = SubElement(body, "WirelessInfo")
            if losing_carrier_account_number:
                SubElement(wireless, "AccountNumber").text = (
                    losing_carrier_account_number.strip()
                )
            if pin:
                SubElement(wireless, "PinNumber").text = pin.strip()
        SubElement(body, "SiteId").text = resolved_site_id
        if peer_id:
            SubElement(body, "PeerId").text = peer_id
        # Partial-port pair goes last, matching Bandwidth's documented example.
        # NewBillingTelephoneNumber only rides along when the BTN is porting and
        # the remainder needs a new one; sending it otherwise is a 7497.
        if partial_port:
            SubElement(body, "PartialPort").text = "true"
            if _clean_tn(new_billing_telephone_number):
                SubElement(body, "NewBillingTelephoneNumber").text = _e164_tn(
                    new_billing_telephone_number
                )
        result = await _dashboard_send(config, "POST", "portins", body, account_id)

        order_id = result.get("id") or ""
        if not order_id:
            for wrapper in ("LnpOrderResponse", "LnpOrder", "order"):
                obj = result.get(wrapper)
                if isinstance(obj, dict):
                    order_id = obj.get("OrderId") or obj.get("id") or ""
                    if order_id:
                        break

        try:
            target_account = _resolve_account(config, account_id)
        except Exception:
            target_account = account_id or config.get("BW_ACCOUNT_ID", "")

        if order_id:
            result["order_id"] = str(order_id)
            if target_account:
                result["order_url"] = (
                    f"https://app.bandwidth.com/a/{target_account}/orders/portIn/{order_id}"
                )

        if loa_file_base64.strip():
            if order_id:
                try:
                    await _upload_port_in_document(
                        config,
                        order_id,
                        file_base64=loa_file_base64,
                        filename=loa_filename or "loa.pdf",
                        document_type="LOA",
                        account_id=account_id,
                    )
                    loa_list = await _dashboard_json(
                        config, f"portins/{order_id}/loas", account_id
                    )
                    result["loa"] = loa_list
                except Exception as exc:
                    result["loa_error"] = (
                        f"Order created successfully, but attaching the LOA failed: {exc}. "
                        f"Retry attaching the LOA with uploadPortInLoa(order_id={order_id!r})."
                    )
            else:
                result["loa_error"] = (
                    "Order created, but no order ID was returned to attach the LOA."
                )

        return result

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
        return await _upload_port_in_document(
            config,
            order_id,
            file_base64,
            filename,
            document_type=document_type,
            content_type=content_type,
            account_id=account_id,
        )

    @mcp.tool(name="supplementPortInOrder", annotations=_WRITE)
    async def supplement_port_in_order(
        order_id: str,
        requested_foc_date: str = "",
        requested_foc_time: str = "",
        site_id: str = "",
        loa_authorizing_person: str = "",
        account_id: str = "",
    ) -> dict:
        """SUPP (modify) an existing port-in order: change the FOC date or time,
        or correct details. Only pass the fields being changed.

        Note: an activation time (requested_foc_time) cannot be added to an
        order that was not filed as Triggered (Bandwidth error 7608).

        Args:
            order_id: The LNP order id (from listPortInOrders).
            requested_foc_date: New requested port date (YYYY-MM-DD).
            requested_foc_time: Optional new requested activation time in 24h
                Eastern time (e.g. "20:00" for 8:00 PM ET). Setting a time
                requires requested_foc_date. Sets Triggered=true. Valid
                windows are 05:00 to 22:00 ET (on-net/internal) and 06:00 to
                22:00 ET (off-net).
            site_id: Corrected destination site.
            loa_authorizing_person: Corrected LOA signer name.
            account_id: Optional account (see listAccounts).
        """
        if requested_foc_time.strip():
            if not requested_foc_date.strip():
                raise ValueError(
                    "requested_foc_time requires requested_foc_date (YYYY-MM-DD) to also be set"
                )
            try:
                t = datetime.strptime(requested_foc_time.strip(), "%H:%M").time()
                if t < time(5, 0) or t > time(22, 0):
                    raise ValueError(
                        f"requested_foc_time: {requested_foc_time.strip()!r} is outside "
                        "Bandwidth's activation windows (automated off-net 06:00-22:00 ET, "
                        "automated on-net and internal 05:00-22:00 ET)"
                    )
            except ValueError as exc:
                if "activation windows" in str(exc):
                    raise
                raise ValueError(
                    f"requested_foc_time: 24h HH:MM in Eastern time, got {requested_foc_time!r}"
                ) from exc

        if requested_foc_date.strip():
            try:
                datetime.strptime(requested_foc_date.strip(), "%Y-%m-%d")
            except ValueError:
                raise ValueError(
                    f"requested_foc_date: YYYY-MM-DD, got {requested_foc_date!r}"
                )

        body = Element("LnpOrderSupp")
        if requested_foc_date.strip():
            foc_date_formatted, is_triggered = _format_foc_date_time(
                requested_foc_date, requested_foc_time
            )
            SubElement(body, "RequestedFocDate").text = foc_date_formatted
            if is_triggered:
                SubElement(body, "Triggered").text = "true"
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
