"""Tests for the spec-driven Numbers XML adapter."""

from xml.etree.ElementTree import tostring, fromstring
import pytest
from pytest_httpx import HTTPXMock

from registry import get_registry
from xml_adapter import dict_to_xml, _resolve_root_tag, execute_numbers_operation
from tools.numbers import _xml_to_data


def test_dict_to_xml_basic():
    data = {
        "@id": "123",
        "Name": "Main Site",
        "Active": True,
        "Count": 5,
    }
    el = dict_to_xml("Site", data)
    assert el.tag == "Site"
    assert el.attrib.get("id") == "123"
    xml_str = tostring(el, encoding="unicode")
    assert "<Name>Main Site</Name>" in xml_str
    assert "<Active>True</Active>" in xml_str
    assert "<Count>5</Count>" in xml_str


def test_dict_to_xml_nested():
    data = {
        "Name": "HQ",
        "Address": {
            "City": "Raleigh",
            "State": "NC",
            "Zip": "27601",
        },
    }
    el = dict_to_xml("Site", data)
    parsed = _xml_to_data(el)
    assert parsed["Name"] == "HQ"
    assert parsed["Address"]["City"] == "Raleigh"
    assert parsed["Address"]["State"] == "NC"
    assert parsed["Address"]["Zip"] == "27601"


def test_dict_to_xml_lists():
    data = {
        "TelephoneNumbers": ["9195551212", "9195551213"],
    }
    el = dict_to_xml("Order", data)
    xml_str = tostring(el, encoding="unicode")
    assert "<TelephoneNumbers><TelephoneNumber>9195551212</TelephoneNumber>" in xml_str
    assert "<TelephoneNumber>9195551213</TelephoneNumber></TelephoneNumbers>" in xml_str


def test_resolve_root_tag():
    reg = get_registry()
    create_site = reg.get_operation("numbers.CreateSite")
    assert create_site is not None
    root_tag = _resolve_root_tag(create_site, {})
    assert root_tag == "Site"

    create_order = reg.get_operation("numbers.CreateOrder")
    assert create_order is not None
    root_order = _resolve_root_tag(create_order, {})
    assert root_order == "Order"


@pytest.mark.asyncio
async def test_execute_numbers_operation_get(httpx_mock: HTTPXMock):
    reg = get_registry()
    op = reg.get_operation("numbers.ListSites")
    assert op is not None

    mock_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<SitesResponse>"
        "  <Sites>"
        "    <Site>"
        "      <Id>1001</Id>"
        "      <Name>Test Site</Name>"
        "    </Site>"
        "  </Sites>"
        "</SitesResponse>"
    )

    httpx_mock.add_response(
        url="https://api.bandwidth.com/api/v2/accounts/5011369/sites",
        status_code=200,
        text=mock_xml,
        headers={"Content-Type": "application/xml"},
    )

    config = {
        "BW_ACCESS_TOKEN": "mock-token",
        "BW_ACCOUNT_ID": "5011369",
        "BW_ACCOUNTS": ["5011369"],
    }

    result = await execute_numbers_operation(op, {}, config)
    assert result.get("status_code") == 200
    sites_resp = result.get("SitesResponse", {})
    site = sites_resp.get("Sites", {}).get("Site", {})
    assert site.get("Id") == "1001"
    assert site.get("Name") == "Test Site"


@pytest.mark.asyncio
async def test_execute_numbers_operation_post_with_location(httpx_mock: HTTPXMock):
    reg = get_registry()
    op = reg.get_operation("numbers.CreateSite")
    assert op is not None

    httpx_mock.add_response(
        method="POST",
        url="https://api.bandwidth.com/api/v2/accounts/5011369/sites",
        status_code=201,
        text="",
        headers={
            "Location": "https://api.bandwidth.com/api/v2/accounts/5011369/sites/9999",
            "Content-Type": "application/xml",
        },
    )

    config = {
        "BW_ACCESS_TOKEN": "mock-token",
        "BW_ACCOUNT_ID": "5011369",
        "BW_ACCOUNTS": ["5011369"],
    }

    args = {
        "Name": "New Site",
        "Description": "Test branch",
    }

    result = await execute_numbers_operation(op, args, config)
    assert result.get("status_code") == 201
    assert result.get("id") == "9999"
    assert (
        result.get("Location")
        == "https://api.bandwidth.com/api/v2/accounts/5011369/sites/9999"
    )


@pytest.mark.asyncio
async def test_numbers_json_serializer_uses_standard_json(httpx_mock: HTTPXMock):
    reg = get_registry()
    op = reg.get_operation("numbers.approveOrDenyMessagingPortoutRequest")
    assert op is not None

    httpx_mock.add_response(
        method="PUT",
        url="https://api.bandwidth.com/api/v2/accounts/5011369/messagingPortouts/req-1",
        status_code=200,
        text="<Response><Status>OK</Status></Response>",
        headers={"Content-Type": "application/xml"},
    )
    config = {"BW_ACCESS_TOKEN": "mock-token", "BW_ACCOUNT_ID": "5011369"}
    args = {"requestId": "req-1", "approval": "yes", "confirm": "APPROVEORDENYMESSAGINGPORTOUTREQUEST"}
    res = await execute_numbers_operation(op, args, config)
    assert res.get("status_code") == 200


def test_create_portin_xml_serializer_uses_lnp_order_root():
    reg = get_registry()
    op = reg.get_operation("numbers.CreatePortin")
    assert op is not None

    payload = {
        "LnpOrder": {
            "SiteId": "2439",
            "PeerId": "23432",
            "LoaAuthorizingPerson": "Test Signer",
            "TargetRespOrgId": "JYT01",
            "ListOfPhoneNumbers": {
                "PhoneNumber": ["+18774809871"]
            }
        }
    }
    root_tag = _resolve_root_tag(op, payload)
    assert root_tag == "LnpOrder"

    # Serializing payload should yield <LnpOrder> root, never <Portin>
    data_for_xml = payload.get(root_tag, payload)
    el = dict_to_xml(root_tag, data_for_xml, op.request_body_schema)
    xml_str = tostring(el, encoding="unicode")
    assert xml_str.startswith("<LnpOrder")
    assert "<Portin" not in xml_str
    assert "<SiteId>2439</SiteId>" in xml_str
    assert "<PhoneNumber>+18774809871</PhoneNumber>" in xml_str


def test_create_disconnect_order_serializes_telephone_number_list_container():
    reg = get_registry()
    op = reg.get_operation("numbers.CreateDisconnectOrder")
    assert op is not None

    payload = {
        "DisconnectTelephoneNumberOrderType": {
            "TelephoneNumberList": ["4158714245", "4352154439"]
        }
    }
    root_tag = _resolve_root_tag(op, payload)
    assert root_tag == "DisconnectTelephoneNumberOrder"

    data_for_xml = payload.get(root_tag, payload)
    el = dict_to_xml(root_tag, data_for_xml, op.request_body_schema)
    xml_str = tostring(el, encoding="unicode")

    # Must serialize as a single TelephoneNumberList containing repeated TelephoneNumber elements
    assert "<TelephoneNumberList><TelephoneNumber>4158714245</TelephoneNumber><TelephoneNumber>4352154439</TelephoneNumber></TelephoneNumberList>" in xml_str


@pytest.mark.asyncio
async def test_upload_portin_loa_file_sends_binary_bytes(httpx_mock: HTTPXMock):
    reg = get_registry()
    op = reg.get_operation("numbers.UploadPortinLoaFile")
    assert op is not None

    import base64
    dummy_pdf = b"%PDF-1.4 sample pdf binary data \x00\xff"
    b64_content = base64.b64encode(dummy_pdf).decode("ascii")

    import httpx
    captured_request = []
    def custom_response(request: httpx.Request):
        captured_request.append(request)
        return httpx.Response(status_code=201, text="<fileUploadResponse><resultCode>0</resultCode></fileUploadResponse>", headers={"Content-Type": "application/xml"})

    httpx_mock.add_callback(custom_response)

    config = {"BW_ACCESS_TOKEN": "mock-token", "BW_ACCOUNT_ID": "5011369"}
    args = {
        "orderId": "03f194d5-3932-4e9f-8ba1-03ef767985e5",
        "documentType": "LOA",
        "file": b64_content,
        "confirm": "UPLOADPORTINLOAFILE"
    }
    res = await execute_numbers_operation(op, args, config)
    assert res.get("status_code") == 201
    assert len(captured_request) == 1
    sent_req = captured_request[0]
    # Must send raw PDF binary bytes, NOT XML string
    assert sent_req.content == dummy_pdf
    assert sent_req.headers.get("content-type") in ("application/pdf", "application/octet-stream")


@pytest.mark.asyncio
async def test_retrieve_portin_loa_file_preserves_binary_download(httpx_mock: HTTPXMock):
    reg = get_registry()
    op = reg.get_operation("numbers.RetrievePortinLoaFile")
    assert op is not None

    import base64
    binary_payload = b"%PDF-1.4 \xff\xfe\x00\x01 non-utf8 binary bytes"

    httpx_mock.add_response(
        method="GET",
        url="https://api.bandwidth.com/api/v2/accounts/5011369/portins/ord-123/loas/signed-loa.pdf",
        status_code=200,
        content=binary_payload,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": 'attachment; filename="signed-loa.pdf"'
        },
    )
    config = {"BW_ACCESS_TOKEN": "mock-token", "BW_ACCOUNT_ID": "5011369"}
    args = {"orderId": "ord-123", "fileId": "signed-loa.pdf"}
    res = await execute_numbers_operation(op, args, config)
    assert res.get("status_code") == 200
    # Binary data preserved losslessly as base64
    assert "data_base64" in res
    recovered = base64.b64decode(res["data_base64"])
    assert recovered == binary_payload
    assert res.get("filename") == "signed-loa.pdf"
    assert res.get("content_type") == "application/octet-stream"
