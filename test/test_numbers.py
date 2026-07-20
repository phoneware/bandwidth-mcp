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
