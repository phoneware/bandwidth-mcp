"""Tests for the TN Options (Call Forwarding) tools."""

import pytest
from fastmcp import FastMCP
from fastmcp.client import Client
from xml.etree.ElementTree import tostring

import tools.tnoptions as tnoptions_mod
from tools.tnoptions import register_tnoptions_tools
from profiles import resolve_profile


def _register(monkeypatch):
    """Register the TN options tools with faked Dashboard I/O; return capture dicts."""
    sent = {}
    reads = []
    reads_abs = []
    json_abs_responses = {}
    json_responses = {}

    async def fake_send(config, method, path, body, account_id=""):
        sent["method"], sent["path"], sent["account_id"] = method, path, account_id
        sent["xml"] = tostring(body, encoding="unicode") if body is not None else None
        return {
            "httpStatus": 201,
            "TnOptionOrderResponse": {
                "TnOptionOrder": {
                    "orderId": "order-1",
                    "ProcessingStatus": "RECEIVED",
                }
            },
        }

    async def fake_json(config, path, account_id=""):
        reads.append((path, account_id))
        return json_responses.get(path, {})

    async def fake_json_abs(config, path):
        reads_abs.append(path)
        return json_abs_responses.get(path, {})

    monkeypatch.setattr(tnoptions_mod, "_dashboard_send", fake_send)
    monkeypatch.setattr(tnoptions_mod, "_dashboard_json", fake_json)
    monkeypatch.setattr(tnoptions_mod, "_dashboard_json_abs", fake_json_abs)

    mcp = FastMCP("t")
    register_tnoptions_tools(mcp, {"BW_ACCESS_TOKEN": "tok", "BW_ACCOUNT_ID": "1"})
    return mcp, sent, reads, reads_abs, json_responses, json_abs_responses


@pytest.mark.asyncio
async def test_set_call_forwarding_builds_correct_xml_and_child_order(monkeypatch):
    mcp, sent, _, _, _, _ = _register(monkeypatch)
    async with Client(mcp) as client:
        await client.call_tool(
            "setCallForwarding",
            {
                "numbers": ["+1 (919) 555-1234", "9195550000"],
                "forward_to": "+1 (919) 555-9999",
                "customer_order_id": "ref-42",
                "confirm": "SETCALLFORWARDING",
            },
        )

    assert sent["method"] == "POST" and sent["path"] == "tnoptions"
    xml = sent["xml"]
    assert "<CustomerOrderId>ref-42</CustomerOrderId>" in xml
    assert "<CallForward>9195559999</CallForward>" in xml
    assert "<TelephoneNumber>9195551234</TelephoneNumber>" in xml
    assert "<TelephoneNumber>9195550000</TelephoneNumber>" in xml

    # Verify child order: CallForward before TelephoneNumbers
    cf_idx = xml.index("<CallForward>")
    tns_idx = xml.index("<TelephoneNumbers>")
    assert cf_idx < tns_idx

    # Verify CustomerOrderId is omitted when not supplied
    async with Client(mcp) as client:
        await client.call_tool(
            "setCallForwarding",
            {
                "numbers": ["9195551234"],
                "forward_to": "9195559999",
                "confirm": "SETCALLFORWARDING",
            },
        )
    assert "CustomerOrderId" not in sent["xml"]


@pytest.mark.asyncio
async def test_set_call_forwarding_empty_forward_to_clears_forwarding(monkeypatch):
    mcp, sent, _, _, _, _ = _register(monkeypatch)
    async with Client(mcp) as client:
        await client.call_tool(
            "setCallForwarding",
            {
                "numbers": ["9195551234"],
                "forward_to": "",
                "confirm": "SETCALLFORWARDING",
            },
        )
    assert "<CallForward>systemDefault</CallForward>" in sent["xml"]

    # Whitespace-only forward_to also clears forwarding
    async with Client(mcp) as client:
        await client.call_tool(
            "setCallForwarding",
            {
                "numbers": ["9195551234"],
                "forward_to": "   ",
                "confirm": "SETCALLFORWARDING",
            },
        )
    assert "<CallForward>systemDefault</CallForward>" in sent["xml"]


@pytest.mark.asyncio
async def test_set_call_forwarding_validates_inputs(monkeypatch):
    mcp, _, _, _, _, _ = _register(monkeypatch)
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="at least one phone number"):
            await client.call_tool(
                "setCallForwarding",
                {
                    "numbers": [],
                    "forward_to": "9195559999",
                    "confirm": "SETCALLFORWARDING",
                },
            )
        with pytest.raises(Exception, match="10-digit phone number"):
            await client.call_tool(
                "setCallForwarding",
                {
                    "numbers": ["9195551234"],
                    "forward_to": "123",
                    "confirm": "SETCALLFORWARDING",
                },
            )
        with pytest.raises(Exception, match="10-digit phone number"):
            await client.call_tool(
                "setCallForwarding",
                {
                    "numbers": ["9195551234"],
                    "forward_to": "not-a-number",
                    "confirm": "SETCALLFORWARDING",
                },
            )


@pytest.mark.asyncio
async def test_set_call_forwarding_requires_confirmation(monkeypatch):
    mcp, _, _, _, _, _ = _register(monkeypatch)
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="SETCALLFORWARDING"):
            await client.call_tool(
                "setCallForwarding",
                {
                    "numbers": ["9195551234"],
                    "forward_to": "9195559999",
                },
            )
@pytest.mark.asyncio
async def test_get_call_forwarding_issues_expected_reads_and_returns_destination(
    monkeypatch,
):
    mcp, _, reads, reads_abs, json_responses, json_abs_responses = _register(
        monkeypatch
    )
    tn = "9195551234"
    json_abs_responses[f"tns/{tn}/tndetails"] = {
        "TelephoneNumberResponse": {
            "TelephoneNumberDetails": {
                "Site": {"Id": "site-101"},
                "SipPeer": {"PeerId": "peer-202"},
                "AccountId": "acct-999",
            }
        }
    }
    json_responses[f"sites/site-101/sippeers/peer-202/tns/{tn}"] = {
        "SipPeerTelephoneNumberResponse": {
            "SipPeerTelephoneNumber": {
                "FullNumber": tn,
                "CallForward": "9195558888",
            }
        }
    }

    async with Client(mcp) as client:
        res = await client.call_tool(
            "getCallForwarding", {"number": "+1 (919) 555-1234"}
        )

    assert reads_abs == [f"tns/{tn}/tndetails"]
    assert reads == [(f"sites/site-101/sippeers/peer-202/tns/{tn}", "acct-999")]
    data = res.data
    assert data["number"] == tn
    assert data["callForward"] == "9195558888"
    assert data["forwarding"] is True
    assert data["siteId"] == "site-101"
    assert data["sipPeerId"] == "peer-202"
    assert data["sipPeerTelephoneNumber"]["CallForward"] == "9195558888"


@pytest.mark.asyncio
async def test_get_call_forwarding_no_call_forward_element_returns_false(monkeypatch):
    mcp, _, reads, reads_abs, json_responses, json_abs_responses = _register(
        monkeypatch
    )
    tn = "9195551234"
    json_abs_responses[f"tns/{tn}/tndetails"] = {
        "TelephoneNumberResponse": {
            "TelephoneNumberDetails": {
                "Site": {"Id": "site-101"},
                "SipPeer": {"PeerId": "peer-202"},
            }
        }
    }
    json_responses[f"sites/site-101/sippeers/peer-202/tns/{tn}"] = {
        "SipPeerTelephoneNumberResponse": {
            "SipPeerTelephoneNumber": {
                "FullNumber": tn,
            }
        }
    }

    async with Client(mcp) as client:
        res = await client.call_tool("getCallForwarding", {"number": tn})

    data = res.data
    assert data["forwarding"] is False
    assert data["callForward"] is None


@pytest.mark.asyncio
async def test_get_call_forwarding_unresolvable_site_or_peer_raises(monkeypatch):
    mcp, _, _, _, _, json_abs_responses = _register(monkeypatch)
    tn = "9195551234"
    json_abs_responses[f"tns/{tn}/tndetails"] = {
        "TelephoneNumberResponse": {
            "TelephoneNumberDetails": {
                "AccountId": "1",
            }
        }
    }
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="Could not resolve site or SIP peer"):
            await client.call_tool("getCallForwarding", {"number": tn})


@pytest.mark.asyncio
async def test_list_tn_option_orders_builds_expected_paths(monkeypatch):
    mcp, _, reads, _, _, _ = _register(monkeypatch)
    async with Client(mcp) as client:
        await client.call_tool("listTnOptionOrders", {})
        await client.call_tool("listTnOptionOrders", {"number": "+1 (919) 555-1234"})
        await client.call_tool(
            "listTnOptionOrders",
            {
                "number": "9195551234",
                "status": "COMPLETE",
            },
        )

    assert reads[0] == ("tnoptions", "")
    assert reads[1] == ("tnoptions?tn=9195551234", "")
    assert reads[2] == ("tnoptions?tn=9195551234&status=COMPLETE", "")


@pytest.mark.asyncio
async def test_get_tn_option_order_path_building_and_validation(monkeypatch):
    mcp, _, reads, _, _, _ = _register(monkeypatch)
    async with Client(mcp) as client:
        await client.call_tool("getTnOptionOrder", {"order_id": "order-abc"})
        with pytest.raises(Exception, match="order_id is required"):
            await client.call_tool("getTnOptionOrder", {"order_id": ""})
        with pytest.raises(Exception, match="order_id is required"):
            await client.call_tool("getTnOptionOrder", {"order_id": "   "})

    assert reads[0] == ("tnoptions/order-abc", "")


def test_tnoptions_tools_in_profiles():
    """Reads ride the numbers profile; the write rides numbers-write. Both
    are in Phoneware's deployed profile set."""
    read_profile = resolve_profile("numbers")
    write_profile = resolve_profile("numbers-write")

    for read_tool in ("getCallForwarding", "listTnOptionOrders", "getTnOptionOrder"):
        assert read_tool in read_profile
        assert read_tool not in write_profile

    assert "setCallForwarding" in write_profile
    assert "setCallForwarding" not in read_profile
