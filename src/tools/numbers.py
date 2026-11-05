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
import hashlib
from copy import deepcopy
from urllib.parse import quote
import re
from datetime import datetime, time
from functools import lru_cache
from xml.etree.ElementTree import Element, SubElement, fromstring, tostring
from zoneinfo import ZoneInfo
import httpx
from fastmcp import Context
from mcp.types import ToolAnnotations

from tools.discovery import _dashboard_get, _resolve_account
from urls import dashboard_api_base
from safety import check_confirmation, elicit_destructive_confirmation
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
    account = _path_segment(_resolve_account(config, account_id))
    url = f"{dashboard_api_base()}/accounts/{account}/{path}"
    async with httpx.AsyncClient(follow_redirects=False) as client:
        resp = await client.post(
            url,
            content=content,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": content_type,
                "Accept": "application/xml",
            },
        )
    if 300 <= resp.status_code < 400:
        raise RuntimeError("Bandwidth returned an upload redirect; no redirect was followed.")
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
def validate_porting_strings(account_number, pin):
    """Trim credentials without coercing numbers or exposing their values."""
    for name, value in (("account number", account_number), ("PIN", pin)):
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string.")
    return account_number.strip(), pin.strip()


def _carrier_assignments(payload):
    assignments = {}
    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if "LosingCarrierSPID" in value and "TnList" in value:
                spid = value["LosingCarrierSPID"]
                tns = value["TnList"]
                tns = tns.get("Tn", []) if isinstance(tns, dict) else []
                if not isinstance(tns, list):
                    tns = [tns]
                if isinstance(spid, str) and spid.strip():
                    for tn in tns:
                        key = _clean_tn(tn)
                        assignments.setdefault(key, set()).add(spid.strip())
            for item in value.values():
                visit(item)
    visit(payload)
    return assignments


async def _portability_request(config, numbers, account_id):
    body = Element("NumberPortabilityRequest")
    _tn_list(body, "TnList", "Tn", numbers, e164=True)
    return await _dashboard_send(config, "POST", "lnpchecker?fullCheck=offnetportability", body, account_id)


async def port_in_advisories(config, numbers, billing_telephone_number, pin="",
                             account_id="", include_loa_warning=False,
                             acknowledge_warnings=None):
    """Return warnings, carrier comparison and a refusal if acknowledgement is missing."""
    _, pin = validate_porting_strings("", pin)
    warnings = []
    preflight = {"status": "unknown", "source": "numbers.RequestPortabilityInfo"}
    try:
        normalized = [_clean_tn(n) for n in numbers]
        btn = _clean_tn(billing_telephone_number)
        if not normalized or not btn or any(len(n) != 10 for n in normalized) or len(btn) != 10:
            raise ValueError("Missing telephone numbers for carrier comparison")
        assignments = _carrier_assignments(await _portability_request(config, numbers, account_id))
        if btn not in normalized:
            for key, values in _carrier_assignments(await _portability_request(config, [billing_telephone_number], account_id)).items():
                assignments.setdefault(key, set()).update(values)
        if any(len(assignments.get(n, set())) != 1 for n in set(normalized + [btn])):
            raise ValueError("Carrier attribution is incomplete or ambiguous")
        btn_spid = next(iter(assignments[btn]))
        mismatch = [n for n in normalized if next(iter(assignments[n])) != btn_spid]
        preflight["status"] = "mismatch" if mismatch else "match"
        if mismatch:
            warnings.append({"code": "BTN_CARRIER_MISMATCH", "message": "The BTN and listed numbers have different losing-carrier SPIDs.", "numbers": mismatch})
    except Exception:
        warnings.append({"code": "CARRIER_LOOKUP_UNAVAILABLE", "message": "Losing-carrier attribution is unavailable or ambiguous."})
    if pin and len(pin) < 4:
        warnings.append({"code": "SHORT_PIN", "message": "The supplied PIN is shorter than four characters."})
    if include_loa_warning:
        warnings.append({"code": "LOA_NOT_ATTACHED", "message": "Filing before attaching a signed LOA may expose the order to rejection."})
    acknowledged = set(acknowledge_warnings or [])
    required = [w["code"] for w in warnings if w["code"] != "LOA_NOT_ATTACHED" and w["code"] not in acknowledged]
    refusal = None
    if required:
        refusal = {"error": "PORT_IN_WARNINGS", "submitted": False, "requires_acknowledgement": True,
                   "warnings": warnings, "required_acknowledgements": required, "carrier_preflight": preflight}
    return warnings, preflight, refusal




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


def _validate_document(file_base64, filename, document_type="LOA", content_type="", file_sha256=""):
    doc_type = document_type.strip().upper() or "LOA"
    if doc_type not in _DOCUMENT_TYPES:
        raise ValueError("Unsupported document type.")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime = content_type.strip() or _UPLOAD_TYPES.get(ext, "")
    if mime not in _UPLOAD_TYPES.values():
        raise ValueError("Unsupported document MIME type.")
    try:
        content = base64.b64decode(file_base64, validate=True)
    except Exception as exc:
        raise ValueError("Invalid base64 document.") from exc
    if not content or len(content) > 3_000_000:
        raise ValueError("Document must contain between 1 and 3000000 bytes.")
    if file_sha256 and (not re.fullmatch(r"[0-9a-fA-F]{64}", file_sha256) or
                        hashlib.sha256(content).hexdigest() != file_sha256.lower()):
        raise ValueError("Document SHA-256 does not match.")
    return content, mime, doc_type


def _path_segment(value):
    if not isinstance(value, str) or not value or value in (".", "..") or "/" in value or "\\" in value:
        raise ValueError("Invalid path identifier.")
    return quote(value, safe="")


async def _upload_port_in_document(config, order_id, file_base64, filename,
                                   document_type="LOA", content_type="",
                                   account_id="", file_sha256=""):
    content, mime, doc_type = _validate_document(file_base64, filename, document_type, content_type, file_sha256)
    order = _path_segment(order_id)
    _path_segment(_resolve_account(config, account_id))
    try:
        uploaded = await _dashboard_upload(
            config, f"portins/{order}/loas?documentType={quote(doc_type)}", content, mime, account_id
        )
    except httpx.TimeoutException as exc:
        raise RuntimeError("Upload outcome unknown. Inspect the order document list before retrying.") from exc
    stored = uploaded.get("id") or _uploaded_filename(uploaded)
    if stored:
        uploaded["filename"] = stored
    return uploaded


def _order_numbers(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "ListOfPhoneNumbers" and isinstance(item, dict):
                found = item.get("PhoneNumber", [])
                return found if isinstance(found, list) else [found]
            found = _order_numbers(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _order_numbers(item)
            if found:
                return found
    return []


def _validate_toll_free_loa(numbers, mime):
    if any(_clean_tn(n)[:3] in {"800", "833", "844", "855", "866", "877", "888"} for n in numbers):
        if mime not in ("application/pdf", "image/tiff"):
            raise ValueError("Toll-free LOAs must be PDF or TIFF.")

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
    # When provided, the replacement is a distinct number left behind on the
    # losing account. A partial port can omit it even if the current BTN ports.
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
            "billing_telephone_number."
        )
    if btn and ported and not partial_port and btn not in ported:
        problems.append(
            f"billing_telephone_number {btn} is not in numbers: a full port has "
            "to include the BTN. Either add it, or set partial_port=true."
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
    async def check_portability(numbers: list[str], account_id: str = "", billing_telephone_number: str = "") -> dict:
        """Check whether numbers CAN port to Bandwidth, and whether they can
        port together on one order. Run this before createPortInOrder.

        Args:
            numbers: Telephone numbers to check (10-digit).
            account_id: Optional account (see listAccounts).
        """
        result = await _portability_request(config, numbers, account_id)
        if billing_telephone_number:
            _, preflight, _ = await port_in_advisories(config, numbers, billing_telephone_number, account_id=account_id)
            result["carrier_preflight"] = preflight
        return result

    # ── carrier writes (numbers-write profile) ──────────────────────────────
    # These are LIVE carrier operations: they buy, remove, and port real
    # service. Confirm intent with the user before calling any of them.

    @mcp.tool(name="orderPhoneNumbers", annotations=_WRITE)
    async def order_phone_numbers(
        numbers: list[str],
        site_id: str,
        peer_id: str = "",
        order_name: str = "",
        confirm: str = "",
        account_id: str = "",
    ) -> dict:
        """ORDER (purchase) specific phone numbers onto the account. This is a
        billable carrier action. Requires confirm='ORDERPHONENUMBERS'.

        Args:
            numbers: The exact numbers to order (from searchAvailableNumbers).
            site_id: Site (sub-account) to place them on (see listSites).
            peer_id: Optional SIP peer/location (see listSipPeers).
            order_name: Optional label for the order.
            confirm: Pass confirm='ORDERPHONENUMBERS' to authorize the purchase.
            account_id: Optional account (see listAccounts).
        """
        conf_err = check_confirmation("orderPhoneNumbers", confirm)
        if conf_err is not None:
            raise RuntimeError(conf_err["error"])
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
        numbers: list[str],
        order_name: str,
        confirm: str = "",
        account_id: str = "",
        ctx: Context = None,
    ) -> dict:
        """DISCONNECT phone numbers: removes them from service. Destructive
        and hard to undo. Requires confirm='DISCONNECTPHONENUMBERS' and MCP elicitation.

        Args:
            numbers: The exact numbers to disconnect.
            order_name: A label for the disconnect order.
            confirm: Pass confirm='DISCONNECTPHONENUMBERS' to authorize disconnection.
            account_id: Optional account (see listAccounts).
        """
        conf_err = check_confirmation("disconnectPhoneNumbers", confirm)
        if conf_err is not None:
            raise RuntimeError(conf_err["error"])
        elicit_err = await elicit_destructive_confirmation(
            "disconnectPhoneNumbers", f"numbers={numbers}, order_name={order_name}", ctx
        )
        if elicit_err is not None:
            raise RuntimeError(elicit_err["error"])
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
        loa_sha256: str = "",
        acknowledge_warnings: list[str] | None = None,
        confirm: str = "",
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

        For a partial port pass partial_port=true. A replacement BTN is optional.
        A full port must include the BTN.

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
            new_billing_telephone_number: Optional replacement BTN when a
                different number remains as the losing account's BTN.
            customer_order_id: Optional reference of yours, echoed back on the
                order (useful for tying a port to a customer ticket).
                Alphanumeric, dashes and spaces only, max 255. Defaults to
                subscriber name plus porting number for single-number ports,
                or subscriber name alone for several.
            loa_file_base64: Optional base64-encoded LOA document (PDF, TIFF,
                PNG, JPEG) to attach in the same call.
            loa_filename: Optional filename for the LOA (e.g. "acme-loa.pdf").
            loa_sha256: Optional SHA-256 digest of the decoded document.
            acknowledge_warnings: Warning codes explicitly reviewed with the user.
                Carrier mismatch, unknown carrier, and short PIN warnings refuse
                submission until acknowledged.
            confirm: Pass confirm='CREATEPORTINORDER' to authorize the port-in order.
            account_id: Optional account (see listAccounts).
        """
        conf_err = check_confirmation("createPortInOrder", confirm)
        if conf_err is not None:
            raise RuntimeError(conf_err["error"])
        losing_carrier_account_number, pin = validate_porting_strings(losing_carrier_account_number, pin)
        if loa_file_base64:
            _, mime, _ = _validate_document(loa_file_base64, loa_filename or "loa.pdf", file_sha256=loa_sha256)
            _validate_toll_free_loa(numbers, mime)
        elif loa_sha256:
            raise ValueError("loa_sha256 requires an inline LOA.")

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
        # Include a replacement BTN only when the operator supplies one.
        if partial_port:
            SubElement(body, "PartialPort").text = "true"
            if _clean_tn(new_billing_telephone_number):
                SubElement(body, "NewBillingTelephoneNumber").text = _e164_tn(
                    new_billing_telephone_number
                )
        warnings, preflight, refusal = await port_in_advisories(
            config, numbers, billing_telephone_number, pin, account_id,
            include_loa_warning=not bool(loa_file_base64),
            acknowledge_warnings=acknowledge_warnings,
        )
        if refusal:
            return refusal
        result = await _dashboard_send(config, "POST", "portins", body, account_id)
        result["warnings"] = warnings
        result["carrier_preflight"] = preflight

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
                        file_sha256=loa_sha256,
                    )
                    loa_list = await _dashboard_json(
                        config, f"portins/{_path_segment(order_id)}/loas", account_id
                    )
                    result["loa"] = loa_list
                except Exception as exc:
                    result["loa_error"] = (
                        f"Order created, but attachment outcome was not confirmed: {exc}. "
                        "Inspect the existing order's document list before retrying; do not recreate the order."
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
        file_sha256: str = "",
        confirm: str = "",
        account_id: str = "",
    ) -> dict:
        """Upload a signed LOA or supporting document onto a port-in order.

        Pass the existing file as base64, or use the local upload helper to
        transfer its bytes without transcribing them through the model. Inspect
        the document list and order status after uploading.

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
        conf_err = check_confirmation("uploadPortInLoa", confirm)
        if conf_err is not None:
            raise RuntimeError(conf_err["error"])
        _, mime, doc_type = _validate_document(file_base64, filename, document_type, content_type, file_sha256)
        if doc_type == "LOA" and mime not in ("application/pdf", "image/tiff"):
            order = await _dashboard_json(config, f"portins/{_path_segment(order_id)}", account_id)
            order_numbers = _order_numbers(order)
            if not order_numbers:
                raise ValueError("Cannot determine whether this order is toll-free; use PDF or TIFF or inspect the order.")
            _validate_toll_free_loa(order_numbers, mime)
        return await _upload_port_in_document(
            config,
            order_id,
            file_base64,
            filename,
            document_type=document_type,
            content_type=content_type,
            account_id=account_id,
            file_sha256=file_sha256,
        )

    @mcp.tool(name="supplementPortInOrder", annotations=_WRITE)
    async def supplement_port_in_order(
        order_id: str,
        requested_foc_date: str = "",
        requested_foc_time: str = "",
        site_id: str = "",
        loa_authorizing_person: str = "",
        losing_carrier_account_number: str | None = None,
        pin: str | None = None,
        subscriber_type: str | None = None,
        business_name: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        house_number: str | None = None,
        street_name: str | None = None,
        address_line_2: str | None = None,
        city: str | None = None,
        state_code: str | None = None,
        zip_code: str | None = None,
        reset_address_fields: bool = False,
        acknowledge_warnings: list[str] | None = None,
        confirm: str = "",
        account_id: str = "",
    ) -> dict:
        """Modify a port-in. Internal orders use fresh read/merge/write.

        Other variants retain legacy sparse updates for date, site and signer,
        which may remove omitted fields. New corrections require INTERNAL.
        """
        conf_err = check_confirmation("supplementPortInOrder", confirm)
        if conf_err is not None:
            raise RuntimeError(conf_err["error"])
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

        values = (losing_carrier_account_number, pin, subscriber_type, business_name,
                  first_name, last_name, house_number, street_name, address_line_2,
                  city, state_code, zip_code)
        if any(value is not None and (not isinstance(value, str) or not value.strip()) for value in values):
            raise ValueError("Changed text fields must be nonempty strings.")
        if not any((requested_foc_date, requested_foc_time, site_id, loa_authorizing_person,
                    reset_address_fields, *values)):
            raise ValueError("Nothing to change.")
        if reset_address_fields and not all((house_number, street_name, city, state_code, zip_code)):
            raise ValueError("Address reset requires house number, street, city, state and ZIP.")
        path = f"portins/{_path_segment(order_id)}"
        new_fields = any(value is not None for value in values) or reset_address_fields
        try:
            current = fromstring(await _dashboard_get(config, path, account_id))
            if current.tag != "LnpOrderResponse":
                current = current.find(".//LnpOrderResponse")
            if current is None:
                raise ValueError("Missing order response")
        except Exception as exc:
            if new_fields:
                raise RuntimeError("No supplement submitted: the current order and its port type could not be read.") from exc
            current = None
        if current is not None and (current.findtext("PortType") or "").strip().upper() == "INTERNAL":
            body = Element("LnpOrderSupp")
            allowed = {
                "CustomerOrderId", "PeerId", "RequestedFocDate", "SiteId", "Tags",
                "BillingTelephoneNumber", "Subscriber", "TnAttributes", "Triggered",
                "LosingResellerName", "LoaAuthorizingPerson", "NewBillingTelephoneNumber",
                "PartialPort", "WirelessInfo",
            }
            if (current.findtext("ProcessingStatus") or "").strip().upper() == "DRAFT":
                allowed.add("AlternateSpid")
            for child in current:
                if child.tag in allowed:
                    body.append(deepcopy(child))

            def put(parent, tag, value):
                node = parent.find(tag)
                if node is None:
                    node = SubElement(parent, tag)
                node.text = value

            if requested_foc_time and (current.findtext("Triggered") or "").strip().lower() != "true":
                raise ValueError("Cannot add an activation time to an order that is not already Triggered.")
            if requested_foc_date:
                formatted, _ = _format_foc_date_time(requested_foc_date, requested_foc_time)
                put(body, "RequestedFocDate", formatted)
            if site_id:
                put(body, "SiteId", site_id.strip())
            if loa_authorizing_person:
                put(body, "LoaAuthorizingPerson", loa_authorizing_person.strip())
            if losing_carrier_account_number is not None or pin is not None:
                wireless = body.find("WirelessInfo")
                if wireless is None:
                    wireless = SubElement(body, "WirelessInfo")
                for tag, value in (("AccountNumber", losing_carrier_account_number), ("PinNumber", pin)):
                    existing = wireless.findtext(tag) or ""
                    if value is None and (not existing.strip() or any(c in existing for c in "*•●")):
                        raise ValueError(f"Cannot preserve WirelessInfo.{tag}: current value is missing or masked.")
                    if value is not None:
                        put(wireless, tag, value.strip())
            wireless = body.find("WirelessInfo")
            if wireless is not None:
                for tag in ("AccountNumber", "PinNumber"):
                    existing = wireless.findtext(tag) or ""
                    if not existing.strip() or any(c in existing for c in "*•●"):
                        raise ValueError(f"Cannot preserve WirelessInfo.{tag}: current value is missing or masked.")
            subscriber_changes = (subscriber_type, business_name, first_name, last_name)
            address_changes = (house_number, street_name, address_line_2, city, state_code, zip_code)
            if any(v is not None for v in subscriber_changes + address_changes) or reset_address_fields:
                subscriber = body.find("Subscriber")
                if subscriber is None:
                    subscriber = SubElement(body, "Subscriber")
                for tag, value in zip(("SubscriberType", "BusinessName", "FirstName", "LastName"), subscriber_changes):
                    if value is not None:
                        put(subscriber, tag, value.strip())
                if any(v is not None for v in address_changes) or reset_address_fields:
                    address = subscriber.find("ServiceAddress")
                    if address is None:
                        address = SubElement(subscriber, "ServiceAddress")
                    if reset_address_fields:
                        address.clear()
                    for tag, value in zip(("HouseNumber", "StreetName", "AddressLine2", "City", "StateCode", "Zip"), address_changes):
                        if value is not None:
                            put(address, tag, value.strip())
                    if reset_address_fields:
                        put(address, "ResetAddressFields", "true")
            numbers = [n.text for n in current.findall("./ListOfPhoneNumbers/PhoneNumber") if n.text]
            warnings, preflight, refusal = await port_in_advisories(
                config, numbers, current.findtext("BillingTelephoneNumber") or "",
                pin or "", account_id, acknowledge_warnings=acknowledge_warnings)
            if refusal:
                return refusal
            result = await _dashboard_send(config, "PUT", path, body, account_id)
            result["warnings"] = warnings
            result["carrier_preflight"] = preflight
            try:
                readback = fromstring(await _dashboard_get(config, path, account_id))
                if readback.tag != "LnpOrderResponse":
                    readback = readback.find(".//LnpOrderResponse")
                if readback is None:
                    raise ValueError("Missing order response")
                result["readback_status"] = (readback.findtext("ProcessingStatus") or "unknown")
                result["verification"] = "pending"
                def leaf_paths(node, prefix=""):
                    for child in node:
                        path_part = f"{prefix}/{child.tag}" if prefix else child.tag
                        if list(child):
                            yield from leaf_paths(child, path_part)
                        elif child.tag != "ResetAddressFields":
                            yield path_part
                differences = []
                for field_path in dict.fromkeys(leaf_paths(body)):
                    expected = [(node.text or "").strip() for node in body.findall(field_path)]
                    actual = [(node.text or "").strip() for node in readback.findall(field_path)]
                    if expected != actual:
                        differences.append(field_path)
                if reset_address_fields:
                    old_address = current.find("Subscriber/ServiceAddress")
                    new_address = body.find("Subscriber/ServiceAddress")
                    if old_address is not None and new_address is not None:
                        for old_field in old_address:
                            if new_address.find(old_field.tag) is None and readback.find(
                                f"Subscriber/ServiceAddress/{old_field.tag}"
                            ) is not None:
                                differences.append(f"Subscriber/ServiceAddress/{old_field.tag}")
                if differences:
                    result["readback_differences"] = differences
                result["message"] = "Supplement accepted; inspect the existing order for final processing. GET and PUT are not atomic."
            except Exception:
                result["verification"] = "unknown"
                result["message"] = "Supplement was submitted but readback failed. Inspect the existing order before retrying."
            return result
        if new_fields:
            raise RuntimeError(
                "No supplement was submitted: the order's writable port variant "
                "and preservation requirements cannot be established safely."
            )
        body = Element("LnpOrderSupp")
        if requested_foc_date.strip():
            foc_date_formatted, is_triggered = _format_foc_date_time(requested_foc_date, requested_foc_time)
            SubElement(body, "RequestedFocDate").text = foc_date_formatted
            if is_triggered:
                SubElement(body, "Triggered").text = "true"
        if site_id:
            SubElement(body, "SiteId").text = site_id
        if loa_authorizing_person:
            SubElement(body, "LoaAuthorizingPerson").text = loa_authorizing_person
        return await _dashboard_send(config, "PUT", f"portins/{_path_segment(order_id)}", body, account_id)

    @mcp.tool(name="cancelPortInOrder", annotations=_DESTRUCTIVE)
    async def cancel_port_in_order(
        order_id: str,
        confirm: str = "",
        account_id: str = "",
        ctx: Context = None,
    ) -> dict:
        """CANCEL a port-in order (only possible before FOC). Destructive:
        the port stops and the order closes. Requires confirm='CANCELPORTINORDER' and MCP elicitation.

        Args:
            order_id: The LNP order id (from listPortInOrders).
            confirm: Pass confirm='CANCELPORTINORDER' to authorize cancellation.
            account_id: Optional account (see listAccounts).
        """
        conf_err = check_confirmation("cancelPortInOrder", confirm)
        if conf_err is not None:
            raise RuntimeError(conf_err["error"])
        elicit_err = await elicit_destructive_confirmation(
            "cancelPortInOrder", f"order_id={order_id}", ctx
        )
        if elicit_err is not None:
            raise RuntimeError(elicit_err["error"])
        return await _dashboard_send(
            config, "DELETE", f"portins/{order_id}", None, account_id
        )
