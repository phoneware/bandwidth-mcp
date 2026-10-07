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
    assert "<TelephoneNumbers>9195551212</TelephoneNumbers>" in xml_str
    assert "<TelephoneNumbers>9195551213</TelephoneNumbers>" in xml_str


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
