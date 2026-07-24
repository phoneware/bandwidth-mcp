"""CNAM (Calling Name) tools over the Bandwidth Dashboard (Numbers) API.

CNAM — the caller-ID name that displays on the phone of the people you call —
is managed on Bandwidth as LIDB (Line Information Data Base) work orders. The
Dashboard LIDB API is XML-based, so from_openapi can't drive it; these are
hand-written tools in the same style as tools/numbers.py, reusing its
authenticated XML helpers.

Endpoints (all under `{api_base}/api/v2/accounts/{accountId}/lidbs`):
  - GET  /lidbs?tn=…      list LIDB orders touching a number   → listLidbOrders
  - GET  /lidbs/{lidbid}  fetch one order's status + name      → getLidbOrder
  - POST /lidbs           set the calling name on TN(s)         → createLidbOrder

There is no CNAM *dip* (look up the name behind an inbound number) in the
Bandwidth spec set — TN Lookup v2 returns carrier/line-type only — so only the
management side is exposed here.
"""

from xml.etree.ElementTree import Element, SubElement

from tools.numbers import _READ, _WRITE, _dashboard_json, _dashboard_send, _tn_list

# Telco CNAM records cap the displayed name at 15 characters. Bandwidth rejects
# longer values, so guard client-side for a clear message instead of a round-trip.
_CNAM_MAX_LEN = 15
_USE_TYPES = ("BUSINESS", "RESIDENTIAL")
_VISIBILITIES = ("PUBLIC", "PRIVATE")


def _normalize_tn(number: str) -> str:
    """Bare 10-digit form the Dashboard API expects (drops +1/country code)."""
    digits = "".join(ch for ch in str(number) if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def register_cnam_tools(mcp, config: dict) -> None:
    """Register CNAM/LIDB tools on the MCP server."""

    @mcp.tool(name="listLidbOrders", annotations=_READ)
    async def list_lidb_orders(number: str, account_id: str = "") -> dict:
        """List CNAM (LIDB) orders that touch a phone number.

        Shows the history of calling-name changes for the number, including
        pending and failed orders. Read-only.

        Args:
            number: The telephone number to look up (10-digit or E.164).
            account_id: Optional account to query (see listAccounts).
        """
        tn = _normalize_tn(number)
        if not tn:
            raise RuntimeError("A phone number is required.")
        return await _dashboard_json(config, f"lidbs?tn={tn}", account_id)

    @mcp.tool(name="getLidbOrder", annotations=_READ)
    async def get_lidb_order(order_id: str, account_id: str = "") -> dict:
        """Get one CNAM (LIDB) order: processing status, the numbers it covers,
        the calling name set, and any per-number errors. Read-only.

        Args:
            order_id: The LIDB order id (from listLidbOrders or createLidbOrder).
            account_id: Optional account to query (see listAccounts).
        """
        if not order_id:
            raise RuntimeError("order_id is required.")
        return await _dashboard_json(config, f"lidbs/{order_id}", account_id)

    @mcp.tool(name="createLidbOrder", annotations=_WRITE)
    async def create_lidb_order(
        numbers: list[str],
        calling_name: str,
        use_type: str = "BUSINESS",
        visibility: str = "PUBLIC",
        customer_order_id: str = "",
        account_id: str = "",
    ) -> dict:
        """SET the CNAM (calling name) on one or more phone numbers. This is a
        billable carrier action that changes the name displayed on outbound
        calls from these numbers. Confirm the exact name and numbers with the
        user first. Applies to numbers you own; propagation is not instant.

        Args:
            numbers: The phone numbers to set the name on (10-digit or E.164).
                All numbers in one call get the same calling_name.
            calling_name: The name to display (max 15 characters).
            use_type: "BUSINESS" (default) or "RESIDENTIAL".
            visibility: "PUBLIC" (default) shows the name on standard calls;
                "PRIVATE" stores it but suppresses the display.
            customer_order_id: Optional reference id for your own tracking
                (alphanumeric, dashes, spaces; max 40 chars).
            account_id: Optional account to target (see listAccounts).
        """
        if not numbers:
            raise RuntimeError("Pass at least one phone number.")
        name = calling_name.strip()
        if not name:
            raise RuntimeError("calling_name (the CNAM) is required.")
        if len(name) > _CNAM_MAX_LEN:
            raise RuntimeError(
                f"calling_name is limited to {_CNAM_MAX_LEN} characters "
                f"(got {len(name)}: {name!r}). Shorten it."
            )
        ut = use_type.strip().upper()
        if ut not in _USE_TYPES:
            raise RuntimeError(f"use_type must be one of {', '.join(_USE_TYPES)}.")
        vis = visibility.strip().upper()
        if vis not in _VISIBILITIES:
            raise RuntimeError(f"visibility must be one of {', '.join(_VISIBILITIES)}.")

        # Body built with ElementTree so user text is XML-escaped, never
        # interpolated. Child order matches the Bandwidth LIDB schema.
        body = Element("LidbOrder")
        if customer_order_id.strip():
            SubElement(body, "CustomerOrderId").text = customer_order_id.strip()
        groups = SubElement(body, "LidbTnGroups")
        group = SubElement(groups, "LidbTnGroup")
        _tn_list(group, "TelephoneNumbers", "TelephoneNumber", numbers)
        SubElement(group, "SubscriberInformation").text = name
        SubElement(group, "UseType").text = ut
        SubElement(group, "Visibility").text = vis
        return await _dashboard_send(config, "POST", "lidbs", body, account_id)
