import pytest
from fastmcp import FastMCP
from pytest_httpx import HTTPXMock
from utils import create_mock, tool_map, server_client
from src.servers import create_bandwidth_mcp, _create_server


async def create_mcp_server(name=None, tools=None, excluded_tools=None):
    """Fixture to create and return a FastMCP instance."""
    mcp = FastMCP(name=name or "Test MCP")
    config = {"BW_ACCESS_TOKEN": "test-bearer-token"}
    enabled_tools = tools if tools is not None else []
    excluded_tools = excluded_tools if excluded_tools is not None else []

    await create_bandwidth_mcp(mcp, enabled_tools, excluded_tools, config)

    return mcp


server_configuration_list = [
    ([], []),
    ([], ["getReports", "createReport"]),
    (["getReports", "createReport"], []),
    (["uploadMedia", "deleteMedia", "getMedia"], ["listMedia"]),
    (["listMedia"], ["uploadMedia", "deleteMedia", "getMedia"]),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tools, excluded_tools", server_configuration_list)
async def test_full_mcp_server_creation(tools, excluded_tools, httpx_mock: HTTPXMock):
    """Test that the MCP server is created correctly with included and excluded tools."""

    for name in [
        "messaging",
        "phone-number-lookup-v2",
        "insights",
        "end-user-management",
        "voice",
        "toll-free-verification",
    ]:
        create_mock(httpx_mock, name)

    mcp = await create_mcp_server("Test MCP", tools, excluded_tools)
    mcp_tools = await tool_map(mcp)
    mcp_tool_names = list(mcp_tools.keys())
    mcp_resources = await mcp.list_resources()

    assert isinstance(mcp, FastMCP)
    assert len(mcp_tools) > 0, "Should have at least some tools loaded"
    assert len(mcp_resources) == 3, f"Expected 3 resources, got {len(mcp_resources)}"

    if excluded_tools:
        for tool in excluded_tools:
            assert tool not in mcp_tool_names, f"Excluded tool {tool} should not be present"

    if tools and not excluded_tools:
        assert len(mcp_tools) == len(tools), f"Expected {len(tools)} tools, got {len(mcp_tools)}"
        for tool in tools:
            assert tool in mcp_tool_names, f"Enabled tool {tool} should be present"


spec_list = [
    (
        "https://dev.bandwidth.com/spec/phone-number-lookup-v2.yml",
        {"BW_ACCESS_TOKEN": "test-token-lookup"},
        "https://api.bandwidth.com/v2/",
        {"createSyncLookup", "createAsyncBulkLookup", "getAsyncBulkLookup"},
        "Bearer test-token-lookup",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url, config, expected_base_url, expected_tools, expected_auth_header", spec_list
)
async def test_individual_mcp_server_creation(
    url, config, expected_base_url, expected_tools, expected_auth_header
):
    """Test that individual MCP servers are created correctly."""

    server = await _create_server(url, None, config)

    server_tools = await tool_map(server)
    server_tool_names = set(server_tools.keys())
    client = await server_client(server)

    assert isinstance(server, FastMCP)
    assert (
        server.name == "Bandwidth"
    ), f"Expected server name to be 'Bandwidth', got '{server.name}'"
    assert (
        server_tool_names == expected_tools
    ), f"Expected tools {expected_tools}, got {server_tool_names}"
    assert (
        client.headers["User-Agent"] == "Bandwidth MCP Server"
    ), f"Expected User-Agent 'Bandwidth MCP Server', got '{client.headers['User-Agent']}'"
    assert (
        client.base_url == expected_base_url
    ), f"Expected base URL '{expected_base_url}', got '{client.base_url}'"
    # Auth attaches per-request from the LIVE config (servers.py
    # _LiveConfigTokenAuth), not as a baked default header — a hosted gateway
    # can mint/refresh the token after boot without a restart.
    assert "Authorization" not in client.headers
    req = client.build_request("GET", "/probe")
    req = next(client.auth.auth_flow(req))
    assert (
        req.headers.get("Authorization") == expected_auth_header
    ), f"Expected auth header '{expected_auth_header}', got '{req.headers.get('Authorization')}'"


@pytest.mark.asyncio
async def test_create_server_no_servers_defined(httpx_mock: HTTPXMock):
    """Test that creating a server with no servers defined raises an error."""

    create_mock(httpx_mock, "no-servers")

    with pytest.raises(ValueError, match="has no servers defined"):
        await _create_server("https://dev.bandwidth.com/spec/no-servers.yml")
