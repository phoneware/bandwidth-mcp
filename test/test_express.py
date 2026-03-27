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


@pytest.mark.asyncio
async def test_create_registration_tool_parameters(httpx_mock):
    """createRegistration tool should require phoneNumber, email, firstName, lastName."""
    create_mock(httpx_mock, "express")

    server = await _create_server(
        url="https://dev.bandwidth.com/spec/express.yml",
        config={},
        requires_auth=False,
    )
    tools = await server.get_tools()
    create_reg = tools["createRegistration"]

    # Access the parameters schema
    params = create_reg.parameters
    assert params is not None
    assert "properties" in params
    assert "phoneNumber" in params["properties"]
    assert "email" in params["properties"]
    assert "firstName" in params["properties"]
    assert "lastName" in params["properties"]
    assert set(params.get("required", [])) == {
        "phoneNumber", "email", "firstName", "lastName"
    }


@pytest.mark.asyncio
async def test_verify_code_tool_parameters(httpx_mock):
    """verifyCode tool should require phoneNumber, code, email."""
    create_mock(httpx_mock, "express")

    server = await _create_server(
        url="https://dev.bandwidth.com/spec/express.yml",
        config={},
        requires_auth=False,
    )
    tools = await server.get_tools()
    verify = tools["verifyCode"]

    # Access the parameters schema
    params = verify.parameters
    assert params is not None
    assert "properties" in params
    assert "phoneNumber" in params["properties"]
    assert "code" in params["properties"]
    assert "email" in params["properties"]
    assert set(params.get("required", [])) == {"phoneNumber", "code", "email"}
