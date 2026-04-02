from pathlib import Path
from typing import List
from fastmcp.resources import FunctionResource, HttpResource, Resource

number_order_guide_resource = HttpResource(
    name="Bandwidth Number Order Guide",
    description="Bandwidth Number Order Guide",
    tags={"bandwidth", "number", "order", "guide"},
    uri="resource://number_order_guide",
    mime_type="text/markdown",
    url="https://dev.bandwidth.com/docs/numbers/guides/searchingForNumbers.md",
)

import specs

_agents_md_path = Path(specs.__file__).parent / "AGENTS.md"

mcp_agent_reference_resource = FunctionResource(
    name="Bandwidth MCP Agent Reference",
    description="Structured reference for AI agents using the Bandwidth MCP Server. Covers available tools, required credentials, common workflows, error patterns, and limitations.",
    tags={"bandwidth", "agent", "reference", "docs"},
    uri="resource://mcp_agent_reference",
    mime_type="text/markdown",
    fn=lambda: _agents_md_path.read_text(encoding="utf-8"),
)


def get_bandwidth_resources() -> List[Resource]:
    """Get all Bandwidth resources."""
    return [number_order_guide_resource, mcp_agent_reference_resource]
