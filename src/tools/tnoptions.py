"""TN Options (Call Forwarding) tools over the Bandwidth Dashboard (Numbers) API.

Call forwarding allows carrier-level redirection of inbound calls on telephone
numbers to another destination number before calls reach SIP peers or PBX
infrastructure. Bandwidth manages call forwarding as asynchronous TN Options
work orders (TnOptionOrder). The Dashboard TN Options API is XML-based, so
from_openapi cannot drive it; these are hand-written tools in the same style
as tools/numbers.py and tools/cnam.py, reusing their authenticated XML helpers.

Endpoints (all under `{api_base}/api/v2/accounts/{accountId}/tnoptions`):
  - POST /tnoptions           submit a TN options order (set/clear call forward) -> setCallForwarding
  - GET  /tnoptions           list TN option orders with optional filters        -> listTnOptionOrders
  - GET  /tnoptions/{orderId} fetch one order's processing status and errors     -> getTnOptionOrder

Current call forwarding settings are read from the per-TN SIP peer record:
  - GET  /tns/{tn}/tndetails  resolve Site ID and SipPeer ID for the number
  - GET  /sites/{siteId}/sippeers/{peerId}/tns/{tn} read SipPeerTelephoneNumber -> getCallForwarding

Route plans and CallForward cannot co-exist on a telephone number. Submitting
CallForward for a number assigned to an origination route plan will cause
errors on processing.
"""

from xml.etree.ElementTree import Element, SubElement

from tools.numbers import (
    _READ,
    _WRITE,
    _clean_tn,
    _dashboard_json,
    _dashboard_json_abs,
    _dashboard_send,
    _sanitize_customer_order_id,
    _tn_list,
)
from safety import check_confirmation


def _nested(payload, *tags: str) -> dict:
    """Walk down a parsed Dashboard response by element name.

    `_xml_to_data` nests every response under its root tag and returns a dict
    for any element that has children, so the happy path is a chain of dict
    lookups. Anything else (a text-only element, a missing tag, an error shape)
    collapses to {} here so the caller reports a legible "could not resolve"
    instead of raising a TypeError three lines later.
    """
    current = payload
    for tag in tags:
        if not isinstance(current, dict):
            return {}
        current = current.get(tag, {})
    return current if isinstance(current, dict) else {}


def register_tnoptions_tools(mcp, config: dict) -> None:
    """Register TN Options / Call Forwarding tools on the MCP server."""

    @mcp.tool(name="setCallForwarding", annotations=_WRITE)
    async def set_call_forwarding(
        numbers: list[str],
        forward_to: str = "",
        customer_order_id: str = "",
        confirm: str = "",
        account_id: str = "",
    ) -> dict:
        """Set or clear carrier-level call forwarding on one or more phone numbers.

        This is a LIVE carrier change on numbers carrying real calls. Confirm
        the exact numbers and destination with the user first.

        The order is asynchronous: this tool submits a TN option order and returns
        the order response. Poll getTnOptionOrder rather than treating the create
        as applied.

        The account needs Bandwidth's CallForwarding product feature (failure
        shows as error 13576). Route plans and CallForward cannot co-exist on a TN.

        To clear forwarding and restore normal routing, leave forward_to empty
        (or blank), which sends systemDefault.

        Args:
            numbers: The phone numbers to forward (10-digit or E.164).
            forward_to: 10-digit destination number to forward calls to. Leave
                empty or blank to clear call forwarding (sends systemDefault).
            customer_order_id: Optional reference ID for tracking (alphanumeric,
                dashes, spaces; max 255 characters).
            confirm: Pass confirm='SETCALLFORWARDING' to authorize the change.
            account_id: Optional account to target (see listAccounts).
        """
        conf_err = check_confirmation("setCallForwarding", confirm)
        if conf_err is not None:
            raise RuntimeError(conf_err["error"])

        if not numbers:
            raise RuntimeError("Pass at least one phone number.")
        ft = forward_to.strip() if forward_to else ""
        if not ft or ft.lower() == "systemdefault":
            cf_value = "systemDefault"
        else:
            cleaned = _clean_tn(ft)
            if len(cleaned) != 10:
                raise RuntimeError(
                    f"forward_to must be a 10-digit phone number (got {forward_to!r})."
                )
            cf_value = cleaned

        body = Element("TnOptionOrder")
        if customer_order_id and customer_order_id.strip():
            sanitized_id = _sanitize_customer_order_id(customer_order_id)
            if sanitized_id:
                SubElement(body, "CustomerOrderId").text = sanitized_id

        groups = SubElement(body, "TnOptionGroups")
        group = SubElement(groups, "TnOptionGroup")
        SubElement(group, "CallForward").text = cf_value
        _tn_list(group, "TelephoneNumbers", "TelephoneNumber", numbers)

        return await _dashboard_send(config, "POST", "tnoptions", body, account_id)

    @mcp.tool(name="getCallForwarding", annotations=_READ)
    async def get_call_forwarding(number: str, account_id: str = "") -> dict:
        """Get the current carrier call forwarding setting on a phone number.

        Resolves the site and SIP peer for the number, then queries the SIP peer
        telephone number record. Returns the forwarding destination and whether
        forwarding is active. Read-only.

        Args:
            number: The phone number to inspect (10-digit or E.164).
            account_id: Optional account to query (see listAccounts).
        """
        tn = _clean_tn(number)
        if not tn:
            raise RuntimeError("A phone number is required.")
        if len(tn) != 10:
            raise RuntimeError(f"A 10-digit phone number is required (got {number!r}).")

        # _xml_to_data nests each response under its root tag, and returns a
        # dict for any element with children, so Site/SipPeer are always dicts
        # when present. Use _nested so a shape we did not expect degrades into
        # the "could not resolve" error below rather than a TypeError.
        details = _nested(
            await _dashboard_json_abs(config, f"tns/{tn}/tndetails"),
            "TelephoneNumberResponse",
            "TelephoneNumberDetails",
        )
        site_id = str(_nested(details, "Site").get("Id") or "").strip()
        peer_id = str(_nested(details, "SipPeer").get("PeerId") or "").strip()

        if not site_id or not peer_id:
            raise RuntimeError(
                f"Could not resolve site or SIP peer for {tn!r} (site_id={site_id!r}, peer_id={peer_id!r})."
            )

        # The TN's own account, not necessarily the primary one. _resolve_account
        # validates it against the token's claims, so a number on an account
        # these credentials cannot reach fails loudly instead of silently
        # reading the wrong account's record.
        target_account = account_id or str(details.get("AccountId") or "")
        sp_tn = _nested(
            await _dashboard_json(
                config, f"sites/{site_id}/sippeers/{peer_id}/tns/{tn}", target_account
            ),
            "SipPeerTelephoneNumberResponse",
            "SipPeerTelephoneNumber",
        )

        # Bandwidth omits CallForward entirely when nothing is set, and reports
        # a cleared forward as systemDefault. Both mean "not forwarding".
        raw_cf = str(sp_tn.get("CallForward") or "").strip()
        forwarding = bool(raw_cf) and raw_cf.lower() != "systemdefault"
        call_forward = raw_cf if forwarding else None

        return {
            "number": tn,
            "callForward": call_forward,
            "forwarding": forwarding,
            "siteId": site_id,
            "sipPeerId": peer_id,
            "sipPeerTelephoneNumber": sp_tn,
        }

    @mcp.tool(name="listTnOptionOrders", annotations=_READ)
    async def list_tn_option_orders(
        number: str = "", status: str = "", account_id: str = ""
    ) -> dict:
        """List TN option orders (such as call forwarding orders) on the account.

        Can filter by telephone number or processing status. Read-only.

        Args:
            number: Optional phone number to filter orders by (10-digit or E.164).
            status: Optional ProcessingStatus to filter by, one of RECEIVED,
                PROCESSING, COMPLETE, PARTIAL, FAILED.
            account_id: Optional account to query (see listAccounts).
        """
        params = []
        if number and number.strip():
            tn = _clean_tn(number)
            if tn:
                params.append(f"tn={tn}")
        if status and status.strip():
            params.append(f"status={status.strip()}")

        path = "tnoptions"
        if params:
            path = f"tnoptions?{'&'.join(params)}"
        return await _dashboard_json(config, path, account_id)

    @mcp.tool(name="getTnOptionOrder", annotations=_READ)
    async def get_tn_option_order(order_id: str, account_id: str = "") -> dict:
        """Get status and details for one TN option order (such as a call forwarding order).

        ProcessingStatus is one of RECEIVED, PROCESSING, COMPLETE, PARTIAL,
        FAILED. PARTIAL means some numbers took the change and others did not,
        so read ErrorList for the per-number failures rather than treating a
        non-FAILED status as success. Read-only.

        Args:
            order_id: The TN option order ID (from setCallForwarding or listTnOptionOrders).
            account_id: Optional account to query (see listAccounts).
        """
        oid = order_id.strip() if order_id else ""
        if not oid:
            raise RuntimeError("order_id is required.")
        return await _dashboard_json(config, f"tnoptions/{oid}", account_id)
