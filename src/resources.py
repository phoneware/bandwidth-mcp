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

# CLI agent reference — describes all bw CLI commands, prerequisites,
# workflows, error patterns, and limitations for AI agent consumption.
_AGENTS_MD = Path(__file__).resolve().parent.parent / "AGENTS.md"

cli_agent_reference = FunctionResource(
    name="Bandwidth CLI Agent Reference",
    description=(
        "Structured reference for AI agents using the bw CLI. "
        "Covers command semantics, prerequisite graph (auth → site → location → app → number → call), "
        "common workflows, error recovery, and limitations. "
        "Read this before using any bw CLI command."
    ),
    tags={"bandwidth", "cli", "agent", "reference", "voice", "bxml"},
    uri="resource://cli_agent_reference",
    mime_type="text/markdown",
    fn=lambda: _AGENTS_MD.read_text() if _AGENTS_MD.exists() else "AGENTS.md not found. Install the bw CLI and place AGENTS.md in the mcp-server root.",
)


def get_bandwidth_resources() -> List[Resource]:
    """Get all Bandwidth resources."""
    return [number_order_guide_resource, cli_agent_reference]
