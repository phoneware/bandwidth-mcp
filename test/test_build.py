import pytest
from utils import create_mock, tool_map, server_client
from src.servers import _create_server


@pytest.mark.asyncio
async def test_build_server_has_one_tool(httpx_mock):
    """Build Registration API should expose exactly 1 tool — createRegistration."""
    create_mock(httpx_mock, "build")

    server = await _create_server(
        url="https://dev.bandwidth.com/spec/build.yml",
        config={},

    )
    tools = await tool_map(server)
    assert len(tools) == 1


@pytest.mark.asyncio
async def test_build_server_tool_name(httpx_mock):
    """Build registration should expose only createRegistration — SMS/email verification happens in the browser."""
    create_mock(httpx_mock, "build")

    server = await _create_server(
        url="https://dev.bandwidth.com/spec/build.yml",
        config={},

    )
    tools = await tool_map(server)
    tool_names = sorted(tools.keys())
    assert tool_names == ["createRegistration"]


@pytest.mark.asyncio
async def test_build_server_no_auth_header(httpx_mock):
    """Build Registration API requires no auth — server should work without credentials."""
    create_mock(httpx_mock, "build")

    server = await _create_server(
        url="https://dev.bandwidth.com/spec/build.yml",
        config={},

    )
    tools = await tool_map(server)
    assert len(tools) == 1
    client = await server_client(server)
    assert "authorization" not in {
        k.lower() for k in client.headers.keys()
    }, "Build registration server should not have an Authorization header"


@pytest.mark.asyncio
async def test_create_registration_tool_parameters(httpx_mock):
    """createRegistration tool should require phoneNumber, email, firstName, lastName."""
    create_mock(httpx_mock, "build")

    server = await _create_server(
        url="https://dev.bandwidth.com/spec/build.yml",
        config={},

    )
    tools = await tool_map(server)
    create_reg = tools["createRegistration"]

    params = create_reg.parameters
    assert params is not None
    assert "properties" in params
    assert "phoneNumber" in params["properties"]
    assert "email" in params["properties"]
    assert "firstName" in params["properties"]
    assert "lastName" in params["properties"]
    assert set(params.get("required", [])) == {
        "phoneNumber",
        "email",
        "firstName",
        "lastName",
    }
