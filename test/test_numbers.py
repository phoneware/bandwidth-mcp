"""Tests for the Numbers/Dashboard tools and tool-surface filtering."""

from xml.etree.ElementTree import fromstring

from src.tools.numbers import _xml_to_data
from src.server_utils import create_route_map_fn
from fastmcp.server.openapi import MCPType


def test_xml_to_data_repeated_tags_become_lists():
    xml = """<LNPResponseWrapper>
      <TotalCount>2</TotalCount>
      <lnpPortInfoForGivenStatuses>
        <lnpPortInfo><OrderId>a1</OrderId><ProcessingStatus>SUBMITTED</ProcessingStatus></lnpPortInfo>
        <lnpPortInfo><OrderId>a2</OrderId><ProcessingStatus>FOC</ProcessingStatus></lnpPortInfo>
      </lnpPortInfoForGivenStatuses>
    </LNPResponseWrapper>"""
    d = _xml_to_data(fromstring(xml))
    assert d["TotalCount"] == "2"
    orders = d["lnpPortInfoForGivenStatuses"]["lnpPortInfo"]
    assert [o["OrderId"] for o in orders] == ["a1", "a2"]


def test_xml_to_data_single_child_stays_dict():
    d = _xml_to_data(fromstring("<Sites><Site><Id>1</Id><Name>Main</Name></Site></Sites>"))
    assert d["Site"]["Name"] == "Main"


class _Route:
    def __init__(self, operation_id):
        self.operation_id = operation_id


def test_route_map_applies_enabled_and_excluded_together():
    """Exclusions must not disable the enabled-list filter (regression: the
    original returned early when excluded_tools was set, loading every spec
    tool except the excluded ones)."""
    fn = create_route_map_fn(["keepMe", "dropMe"], ["dropMe"])
    keep = fn(_Route("keepMe"), MCPType.TOOL)
    dropped_excluded = fn(_Route("dropMe"), MCPType.TOOL)
    dropped_unlisted = fn(_Route("neverListed"), MCPType.TOOL)
    assert keep == MCPType.TOOL
    assert dropped_excluded == MCPType.EXCLUDE
    assert dropped_unlisted == MCPType.EXCLUDE


import pytest
from fastmcp import FastMCP
from fastmcp.client import Client
from xml.etree.ElementTree import tostring

import src.tools.numbers as numbers_mod
from src.tools.numbers import register_numbers_tools


@pytest.mark.asyncio
async def test_write_tools_build_correct_escaped_xml(monkeypatch):
    sent = {}

    async def fake_send(config, method, path, body, account_id=""):
        sent["method"], sent["path"] = method, path
        sent["xml"] = tostring(body, encoding="unicode") if body is not None else None
        return {"httpStatus": 201, "id": "order-1"}

    monkeypatch.setattr(numbers_mod, "_dashboard_send", fake_send)
    mcp = FastMCP("t")
    register_numbers_tools(mcp, {"BW_ACCESS_TOKEN": "tok", "BW_ACCOUNT_ID": "1"})

    async with Client(mcp) as client:
        await client.call_tool("orderPhoneNumbers", {
            "numbers": ["+1 (919) 555-1234"], "site_id": "s1",
            "order_name": 'Rick & "Jason" <order>'})
        assert sent["method"] == "POST" and sent["path"] == "orders"
        assert "<TelephoneNumber>9195551234</TelephoneNumber>" in sent["xml"]
        # user text must be escaped, never raw XML
        assert "&amp;" in sent["xml"] and "<order>" not in sent["xml"]

        await client.call_tool("disconnectPhoneNumbers", {
            "numbers": ["9195551234"], "order_name": "cleanup"})
        assert sent["path"] == "disconnects"
        assert "<DisconnectTelephoneNumberOrderType>" in sent["xml"]

        await client.call_tool("createPortInOrder", {
            "billing_telephone_number": "1-919-555-0000",
            "numbers": ["9195550000"], "site_id": "s1",
            "loa_authorizing_person": "Rick Waldrip",
            "business_name": "Phoneware", "house_number": "1", "street_name": "Main",
            "city": "Phoenix", "state_code": "AZ", "zip_code": "85001"})
        assert sent["path"] == "portins"
        assert "<BillingTelephoneNumber>9195550000</BillingTelephoneNumber>" in sent["xml"]
        assert "<SubscriberType>BUSINESS</SubscriberType>" in sent["xml"]

        await client.call_tool("cancelPortInOrder", {"order_id": "ord-9"})
        assert sent["method"] == "DELETE" and sent["path"] == "portins/ord-9"
        assert sent["xml"] is None


@pytest.mark.asyncio
async def test_portin_portout_lists_always_send_page_and_size(monkeypatch):
    """Bandwidth 404s /portins and /portouts without explicit page+size
    (discovered live; the 404 body advertises the paged link)."""
    paths = []

    async def fake_json(config, path, account_id=""):
        paths.append(path)
        return {}

    monkeypatch.setattr(numbers_mod, "_dashboard_json", fake_json)
    mcp = FastMCP("t")
    register_numbers_tools(mcp, {"BW_ACCESS_TOKEN": "tok", "BW_ACCOUNT_ID": "1"})

    async with Client(mcp) as client:
        await client.call_tool("listPortInOrders", {})
        await client.call_tool("listPortInOrders", {"status": "pending", "size": 50})
        await client.call_tool("listPortOutOrders", {})
    assert paths[0] == "portins?page=1&size=300"
    assert paths[1].startswith("portins?page=1&size=50&status=")
    assert paths[2] == "portouts?page=1&size=300"


@pytest.mark.asyncio
async def test_number_orders_paged_and_lnpchecker_e164(monkeypatch):
    paths, bodies = [], []

    async def fake_json(config, path, account_id=""):
        paths.append(path)
        return {}

    async def fake_send(config, method, path, body, account_id=""):
        bodies.append((path, tostring(body, encoding="unicode")))
        return {}

    monkeypatch.setattr(numbers_mod, "_dashboard_json", fake_json)
    monkeypatch.setattr(numbers_mod, "_dashboard_send", fake_send)
    mcp = FastMCP("t")
    register_numbers_tools(mcp, {"BW_ACCESS_TOKEN": "tok", "BW_ACCOUNT_ID": "1"})
    async with Client(mcp) as client:
        await client.call_tool("listNumberOrders", {})
        await client.call_tool("checkPortability", {"numbers": ["(480) 528-7344"]})
    assert paths[0] == "orders?page=1&size=300"
    path, xml = bodies[0]
    assert path.startswith("lnpchecker")
    # lnpchecker is E.164; every other endpoint takes bare 10-digit
    assert "<Tn>+14805287344</Tn>" in xml
