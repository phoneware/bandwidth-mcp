import pytest
from utils import create_mock
from src.servers import _create_server


@pytest.mark.asyncio
async def test_express_server_has_three_tools(httpx_mock):
    """Express Registration API should expose exactly 3 tools."""
    create_mock(httpx_mock, "express")

    server = await _create_server(
        url="https://dev.bandwidth.com/spec/express.yml",
        config={"BW_USERNAME": "user", "BW_PASSWORD": "pass"},
    )
    tools = await server.get_tools()
    assert len(tools) == 3


@pytest.mark.asyncio
async def test_express_server_tool_names(httpx_mock):
    """Express tools should have correct operation IDs."""
    create_mock(httpx_mock, "express")

    server = await _create_server(
        url="https://dev.bandwidth.com/spec/express.yml",
        config={"BW_USERNAME": "user", "BW_PASSWORD": "pass"},
    )
    tools = await server.get_tools()
    tool_names = sorted(tools.keys())
    assert tool_names == ["createRegistration", "sendVerificationCode", "verifyCode"]


@pytest.mark.asyncio
async def test_express_server_no_auth_header(httpx_mock):
    """Express API requires no auth — server should work without credentials."""
    create_mock(httpx_mock, "express")

    server = await _create_server(
        url="https://dev.bandwidth.com/spec/express.yml",
        config={},
        requires_auth=False,
    )
    tools = await server.get_tools()
    assert len(tools) == 3
    assert "authorization" not in {
        k.lower() for k in server._client.headers.keys()
    }, "Express server should not have an Authorization header"
