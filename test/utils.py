from pytest_httpx import HTTPXMock


async def tool_map(mcp):
    """Return {tool_name: tool} for a FastMCP server.

    fastmcp 3.x replaced the dict-returning get_tools() with list_tools()
    (a list of Tool objects). This restores the name→tool mapping the tests
    rely on.
    """
    return {tool.name: tool for tool in await mcp.list_tools()}


async def server_client(mcp):
    """Return the shared httpx client backing a from_openapi server's tools.

    fastmcp 3.x stores the client per-tool at tool._client (was server._client
    in 2.x). All tools from one _create_server share the same client, so the
    first tool's is representative.
    """
    tools = await mcp.list_tools()
    return tools[0]._client


def create_mock(httpx_mock: HTTPXMock, spec_name: str):
    """Helper function to create a mock response for HTTPX."""
    with open(f"test/fixtures/{spec_name}.yml", "r", encoding="utf-8") as f:
        response_text = f.read()
    httpx_mock.add_response(
        url=f"https://dev.bandwidth.com/spec/{spec_name}.yml", text=response_text
    )
