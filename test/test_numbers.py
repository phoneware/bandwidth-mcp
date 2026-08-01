"""Tests for the Numbers/Dashboard tools and tool-surface filtering."""

from xml.etree.ElementTree import fromstring

from src.tools.numbers import _xml_to_data
from src.server_utils import create_route_map_fn
from fastmcp.server.providers.openapi import MCPType


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


# ── port-in validation ──────────────────────────────────────────────────────
# Bandwidth requires the subscriber name and the full service address on an
# LNP order. They used to be optional kwargs that defaulted to "", so an
# under-specified call shipped empty XML elements at a live carrier write.

_GOOD_PORT_IN = {
    "billing_telephone_number": "9195550000",
    "numbers": ["9195550000", "9195550001"],
    "site_id": "s1",
    "loa_authorizing_person": "Rick Waldrip",
    "business_name": "Phoneware",
    "house_number": "1",
    "street_name": "Main",
    "city": "Phoenix",
    "state_code": "AZ",
    "zip_code": "85001",
}


def _problems(**overrides):
    args = {**_GOOD_PORT_IN, **overrides}
    return numbers_mod._port_in_problems(
        args["numbers"],
        args["billing_telephone_number"],
        args.get("business_name", ""),
        args.get("first_name", ""),
        args.get("last_name", ""),
        args.get("house_number", ""),
        args.get("street_name", ""),
        args.get("city", ""),
        args.get("state_code", ""),
        args.get("zip_code", ""),
        args.get("requested_foc_date", ""),
        args.get("partial_port", False),
        args.get("new_billing_telephone_number", ""),
    )


def test_complete_port_in_has_no_problems():
    assert _problems() == []
    # residential form: first + last instead of a business name
    assert _problems(business_name="", first_name="Rick", last_name="Waldrip") == []


def test_port_in_requires_subscriber_and_service_address():
    missing_all = _problems(
        business_name="", house_number="", street_name="", city="",
        state_code="", zip_code="",
    )
    joined = " ".join(missing_all)
    assert "subscriber" in joined
    for field in ("house_number", "street_name", "city", "state_code", "zip_code"):
        assert field in joined
    # half a residential name is not a name
    assert any("subscriber" in p for p in _problems(business_name="", first_name="Rick"))


def test_port_in_validates_formats():
    assert any("state_code" in p for p in _problems(state_code="Arizona"))
    assert any("zip_code" in p for p in _problems(zip_code="8500"))
    assert any("requested_foc_date" in p for p in _problems(requested_foc_date="9/1/26"))
    assert _problems(zip_code="85001-1234", requested_foc_date="2026-09-01") == []
    assert any("10-digit" in p for p in _problems(numbers=["919555"]))
    assert any("numbers" in p for p in _problems(numbers=[]))


def test_port_in_partial_port_rules():
    # partial port without the TN that stays behind
    assert any(
        "new_billing_telephone_number" in p
        for p in _problems(numbers=["9195550001"], partial_port=True)
    )
    # the new BTN cannot be one of the numbers leaving
    assert any(
        "staying with the losing carrier" in p
        for p in _problems(
            numbers=["9195550001"],
            partial_port=True,
            new_billing_telephone_number="9195550001",
        )
    )
    # the flag has to be set explicitly
    assert any(
        "partial_port" in p
        for p in _problems(new_billing_telephone_number="9195550000")
    )
    # a full port has to carry the BTN
    assert any(
        "not in numbers" in p for p in _problems(numbers=["9195550001"])
    )
    assert (
        _problems(
            numbers=["9195550001"],
            partial_port=True,
            new_billing_telephone_number="9195550000",
        )
        == []
    )


@pytest.mark.asyncio
async def test_create_port_in_refuses_incomplete_order_without_calling_bandwidth(
    monkeypatch,
):
    """The whole point: an under-specified port-in never reaches the carrier."""
    calls = []

    async def fake_send(config, method, path, body, account_id=""):
        calls.append(path)
        return {"httpStatus": 201, "id": "order-1"}

    monkeypatch.setattr(numbers_mod, "_dashboard_send", fake_send)
    mcp = FastMCP("t")
    register_numbers_tools(mcp, {"BW_ACCESS_TOKEN": "tok", "BW_ACCOUNT_ID": "1"})

    async with Client(mcp) as client:
        with pytest.raises(Exception) as err:
            await client.call_tool("createPortInOrder", {
                "billing_telephone_number": "9195550000",
                "numbers": ["9195550000"],
                "site_id": "s1",
                "loa_authorizing_person": "Rick Waldrip",
            })
    assert calls == []
    message = str(err.value)
    assert "service address" in message and "subscriber" in message


@pytest.mark.asyncio
async def test_create_port_in_emits_partial_port_pair(monkeypatch):
    sent = {}

    async def fake_send(config, method, path, body, account_id=""):
        sent["xml"] = tostring(body, encoding="unicode")
        return {"httpStatus": 201, "id": "order-1"}

    monkeypatch.setattr(numbers_mod, "_dashboard_send", fake_send)
    mcp = FastMCP("t")
    register_numbers_tools(mcp, {"BW_ACCESS_TOKEN": "tok", "BW_ACCOUNT_ID": "1"})

    async with Client(mcp) as client:
        await client.call_tool("createPortInOrder", {
            **_GOOD_PORT_IN,
            "numbers": ["9195550001"],
            "partial_port": True,
            "new_billing_telephone_number": "+1 (919) 555-0000",
            "customer_order_id": "TICKET-42",
            "state_code": "az",
        })
    xml = sent["xml"]
    assert "<PartialPort>true</PartialPort>" in xml
    assert "<NewBillingTelephoneNumber>9195550000</NewBillingTelephoneNumber>" in xml
    assert "<CustomerOrderId>TICKET-42</CustomerOrderId>" in xml
    assert "<StateCode>AZ</StateCode>" in xml


# ── LOA upload ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_port_in_loa_posts_binary_then_tags_document_type(monkeypatch):
    import base64

    uploads = []
    sends = []

    async def fake_upload(config, path, content, content_type, account_id=""):
        uploads.append((path, content, content_type))
        return {"httpStatus": 201, "FileUploadResponse": {"filename": "stored-1.pdf"}}

    async def fake_send(config, method, path, body, account_id=""):
        sends.append((method, path, tostring(body, encoding="unicode")))
        return {"httpStatus": 200}

    monkeypatch.setattr(numbers_mod, "_dashboard_upload", fake_upload)
    monkeypatch.setattr(numbers_mod, "_dashboard_send", fake_send)
    mcp = FastMCP("t")
    register_numbers_tools(mcp, {"BW_ACCESS_TOKEN": "tok", "BW_ACCOUNT_ID": "1"})

    async with Client(mcp) as client:
        result = await client.call_tool("uploadPortInLoa", {
            "order_id": "ord-9",
            "file_base64": base64.b64encode(b"%PDF-1.7 signed").decode(),
            "filename": "acme-loa.pdf",
        })

    path, content, content_type = uploads[0]
    assert path == "portins/ord-9/loas"
    assert content == b"%PDF-1.7 signed"
    assert content_type == "application/pdf"
    method, meta_path, meta_xml = sends[0]
    assert method == "PUT"
    assert meta_path == "portins/ord-9/loas/stored-1.pdf/metadata"
    assert "<DocumentType>LOA</DocumentType>" in meta_xml
    assert result.data["filename"] == "stored-1.pdf"


@pytest.mark.asyncio
async def test_upload_port_in_loa_reports_metadata_failure_without_losing_the_file(
    monkeypatch,
):
    """The file is already stored by then — surface the problem, don't raise."""
    import base64

    async def fake_upload(config, path, content, content_type, account_id=""):
        return {"httpStatus": 201, "FileUploadResponse": {"filename": "stored-1.pdf"}}

    async def fake_send(config, method, path, body, account_id=""):
        raise RuntimeError("Bandwidth rejected the request (400): bad metadata")

    monkeypatch.setattr(numbers_mod, "_dashboard_upload", fake_upload)
    monkeypatch.setattr(numbers_mod, "_dashboard_send", fake_send)
    mcp = FastMCP("t")
    register_numbers_tools(mcp, {"BW_ACCESS_TOKEN": "tok", "BW_ACCOUNT_ID": "1"})

    async with Client(mcp) as client:
        result = await client.call_tool("uploadPortInLoa", {
            "order_id": "ord-9",
            "file_base64": base64.b64encode(b"pdf").decode(),
            "filename": "loa.pdf",
        })
    assert "bad metadata" in result.data["metadataError"]
    assert result.data["filename"] == "stored-1.pdf"


@pytest.mark.asyncio
async def test_upload_port_in_loa_rejects_bad_input(monkeypatch):
    import base64

    calls = []

    async def fake_upload(config, path, content, content_type, account_id=""):
        calls.append(path)
        return {"httpStatus": 201}

    monkeypatch.setattr(numbers_mod, "_dashboard_upload", fake_upload)
    mcp = FastMCP("t")
    register_numbers_tools(mcp, {"BW_ACCESS_TOKEN": "tok", "BW_ACCOUNT_ID": "1"})

    good = base64.b64encode(b"pdf").decode()
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="document_type"):
            await client.call_tool("uploadPortInLoa", {
                "order_id": "o", "file_base64": good, "filename": "a.pdf",
                "document_type": "CONTRACT"})
        with pytest.raises(Exception, match="content_type"):
            await client.call_tool("uploadPortInLoa", {
                "order_id": "o", "file_base64": good, "filename": "loa.docx"})
        with pytest.raises(Exception, match="base64"):
            await client.call_tool("uploadPortInLoa", {
                "order_id": "o", "file_base64": "not base64!!", "filename": "a.pdf"})
    assert calls == []


def test_loa_tools_live_in_the_right_profiles():
    from src.profiles import PROFILES

    assert "listPortInLoas" in PROFILES["numbers"]
    assert "uploadPortInLoa" in PROFILES["numbers-write"]
    assert "uploadPortInLoa" not in PROFILES["numbers"]


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
