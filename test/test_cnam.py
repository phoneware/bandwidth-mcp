"""Tests for the CNAM (LIDB) tools."""

import pytest
from fastmcp import FastMCP
from fastmcp.client import Client
from xml.etree.ElementTree import tostring

import src.tools.cnam as cnam_mod
from src.tools.cnam import register_cnam_tools
from src.profiles import resolve_profile


def _register(monkeypatch):
    """Register the CNAM tools with faked Dashboard I/O; return capture dicts."""
    sent = {}
    reads = []

    async def fake_send(config, method, path, body, account_id=""):
        sent["method"], sent["path"], sent["account_id"] = method, path, account_id
        sent["xml"] = tostring(body, encoding="unicode") if body is not None else None
        return {"httpStatus": 201, "LidbOrderResponse": {"LidbOrder": {"orderId": "lidb-1"}}}

    async def fake_json(config, path, account_id=""):
        reads.append((path, account_id))
        return {}

    monkeypatch.setattr(cnam_mod, "_dashboard_send", fake_send)
    monkeypatch.setattr(cnam_mod, "_dashboard_json", fake_json)
    mcp = FastMCP("t")
    register_cnam_tools(mcp, {"BW_ACCESS_TOKEN": "tok", "BW_ACCOUNT_ID": "1"})
    return mcp, sent, reads


@pytest.mark.asyncio
async def test_create_lidb_order_builds_correct_escaped_xml(monkeypatch):
    mcp, sent, _ = _register(monkeypatch)
    async with Client(mcp) as client:
        await client.call_tool("createLidbOrder", {
            "numbers": ["+1 (919) 555-1234", "9195550000"],
            "calling_name": "Joe & Co",
            "use_type": "residential",   # lower-case must normalize
            "visibility": "private",
            "customer_order_id": "ref-42",
        })
    assert sent["method"] == "POST" and sent["path"] == "lidbs"
    xml = sent["xml"]
    # numbers normalized to bare 10-digit, both in one TN group
    assert "<TelephoneNumber>9195551234</TelephoneNumber>" in xml
    assert "<TelephoneNumber>9195550000</TelephoneNumber>" in xml
    # user text escaped, never raw XML
    assert "<SubscriberInformation>Joe &amp; Co</SubscriberInformation>" in xml
    # enums upper-cased
    assert "<UseType>RESIDENTIAL</UseType>" in xml
    assert "<Visibility>PRIVATE</Visibility>" in xml
    assert "<CustomerOrderId>ref-42</CustomerOrderId>" in xml


@pytest.mark.asyncio
async def test_create_lidb_order_child_order_matches_schema(monkeypatch):
    """Bandwidth's LIDB schema is order-sensitive: TelephoneNumbers,
    SubscriberInformation, UseType, Visibility."""
    mcp, sent, _ = _register(monkeypatch)
    async with Client(mcp) as client:
        await client.call_tool("createLidbOrder", {
            "numbers": ["9195551234"], "calling_name": "ACME"})
    xml = sent["xml"]
    order = [
        xml.index("<TelephoneNumbers>"),
        xml.index("<SubscriberInformation>"),
        xml.index("<UseType>"),
        xml.index("<Visibility>"),
    ]
    assert order == sorted(order)
    # defaults applied
    assert "<UseType>BUSINESS</UseType>" in xml
    assert "<Visibility>PUBLIC</Visibility>" in xml
    # no CustomerOrderId element when not supplied
    assert "CustomerOrderId" not in xml


@pytest.mark.asyncio
async def test_create_lidb_order_rejects_overlong_name(monkeypatch):
    mcp, _, _ = _register(monkeypatch)
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="15 characters"):
            await client.call_tool("createLidbOrder", {
                "numbers": ["9195551234"],
                "calling_name": "This Name Is Way Too Long"})


@pytest.mark.asyncio
async def test_create_lidb_order_validates_enums(monkeypatch):
    mcp, _, _ = _register(monkeypatch)
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="use_type"):
            await client.call_tool("createLidbOrder", {
                "numbers": ["9195551234"], "calling_name": "OK", "use_type": "GOV"})
        with pytest.raises(Exception, match="visibility"):
            await client.call_tool("createLidbOrder", {
                "numbers": ["9195551234"], "calling_name": "OK", "visibility": "SECRET"})


@pytest.mark.asyncio
async def test_create_lidb_order_requires_numbers_and_name(monkeypatch):
    mcp, _, _ = _register(monkeypatch)
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="at least one phone number"):
            await client.call_tool("createLidbOrder", {
                "numbers": [], "calling_name": "ACME"})
        with pytest.raises(Exception, match="calling_name"):
            await client.call_tool("createLidbOrder", {
                "numbers": ["9195551234"], "calling_name": "   "})


@pytest.mark.asyncio
async def test_read_tools_normalize_tn_and_build_paths(monkeypatch):
    mcp, _, reads = _register(monkeypatch)
    async with Client(mcp) as client:
        await client.call_tool("listLidbOrders", {"number": "+1 (919) 555-1234"})
        await client.call_tool("getLidbOrder", {"order_id": "lidb-9"})
    assert reads[0] == ("lidbs?tn=9195551234", "")
    assert reads[1] == ("lidbs/lidb-9", "")


def test_cnam_tools_in_numbers_profiles():
    """Reads ride the numbers profile; the write rides numbers-write — both
    are in Phoneware's deployed profile set."""
    read_profile = resolve_profile("numbers")
    assert "listLidbOrders" in read_profile
    assert "getLidbOrder" in read_profile
    assert "createLidbOrder" not in read_profile  # write stays out of read profile

    write_profile = resolve_profile("numbers-write")
    assert "createLidbOrder" in write_profile
