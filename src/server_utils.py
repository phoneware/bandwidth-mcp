import copy
import hashlib
import warnings
import yaml
import httpx
import base64

from pathlib import Path
from fastmcp import FastMCP
from fastmcp.resources import FunctionResource
from fastmcp.server.openapi import MCPType, HTTPRoute
from typing import Dict, List, Optional, Any, Callable

from resources import get_bandwidth_resources

CACHE_DIR = Path.home() / ".bw-mcp" / "spec-cache"


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16] + ".yml"


def _save_spec_cache(url: str, spec: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / _cache_key(url)
    cache_path.write_text(yaml.dump(spec), encoding="utf-8")


def _load_spec_cache(url: str) -> dict | None:
    cache_path = CACHE_DIR / _cache_key(url)
    if not cache_path.exists():
        return None
    try:
        return yaml.safe_load(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None


async def print_server_info(mcp: FastMCP) -> None:
    """Print concise server information."""

    all_tools = await mcp.get_tools()
    all_resources = await mcp.get_resources()

    tool_names = list(all_tools.keys())
    resource_names = [resource.name for resource in all_resources.values()]

    print("Bandwidth MCP Server Started")
    print(
        f"Tools ({len(tool_names)}): {', '.join(sorted(tool_names)) if tool_names else 'None'}"
    )
    print(
        f"Resources ({len(resource_names)}): {', '.join(sorted(resource_names)) if resource_names else 'None'}"
    )


def create_route_map_fn(
    enabled_tools: Optional[List[str]], excluded_tools: Optional[List[str]]
) -> Callable[[HTTPRoute, MCPType], MCPType]:
    """Create a route map function based on enabled and excluded tools.

    Args:
        enabled_tools: List of tools to enable. If None, all tools are enabled.
        excluded_tools: List of tools to exclude. Takes priority over enabled_tools.

    Returns:
        A function that maps routes to MCP types based on the tool configuration.
    """

    def route_map_fn(route: HTTPRoute, mcp_type: MCPType) -> MCPType:
        # Excluded tools have priority - if provided, ignore enabled tools
        if excluded_tools:
            return (
                mcp_type
                if route.operation_id not in excluded_tools
                else MCPType.EXCLUDE
            )
        if enabled_tools:
            return mcp_type if route.operation_id in enabled_tools else MCPType.EXCLUDE

        return mcp_type

    return route_map_fn


def _clean_openapi_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively clean OpenAPI spec:
    - Remove all callbacks
    - Remove all 4xx/5xx responses
    - Remove any field starting with 'x-'
    - Remove all path resources that start with 'x-'
    """
    cleaned_spec = copy.deepcopy(spec)

    def _clean(obj: Any) -> Any:
        if isinstance(obj, dict):
            # Remove 'callbacks' and 'x-' fields
            keys_to_remove = [k for k in obj if k == "callbacks" or k.startswith("x-")]
            for k in keys_to_remove:
                del obj[k]
            # Remove 4xx/5xx responses
            if "responses" in obj:
                codes_to_remove = [
                    code
                    for code in obj["responses"]
                    if str(code).startswith(("4", "5"))
                ]
                for code in codes_to_remove:
                    del obj["responses"][code]
            # Special handling for paths
            if "paths" in obj:
                paths_to_remove = [p for p in obj["paths"] if p.startswith("x-")]
                for p in paths_to_remove:
                    del obj["paths"][p]
            # Recurse into all values
            for v in obj.values():
                _clean(v)
        elif isinstance(obj, list):
            for item in obj:
                _clean(item)
        return obj

    return _clean(cleaned_spec)


async def fetch_openapi_spec(url: str) -> Dict[str, Any]:
    """Fetch and parse OpenAPI spec from URL or local file, with cache fallback."""
    # Local file path — read directly, no caching needed
    local_path = Path(url)
    if local_path.exists():
        try:
            spec_object = yaml.safe_load(local_path.read_text(encoding="utf-8"))
            if not spec_object:
                raise ValueError(f"Empty or invalid YAML spec from {url}")
            return _clean_openapi_spec(spec_object)
        except yaml.YAMLError as e:
            raise RuntimeError(f"Failed to parse local spec {url}: {e}") from e

    # Remote URL — fetch, cache, fallback
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            response.raise_for_status()
            spec_text = response.text
        spec_object = yaml.safe_load(spec_text)
        if not spec_object:
            raise ValueError(f"Empty or invalid YAML spec from {url}")
        cleaned = _clean_openapi_spec(spec_object)
        _save_spec_cache(url, cleaned)
        return cleaned
    except (ValueError, yaml.YAMLError):
        raise
    except Exception as e:
        cached = _load_spec_cache(url)
        if cached:
            warnings.warn(f"Using cached spec for {url}: {e}")
            return cached
        raise RuntimeError(f"Failed to fetch OpenAPI spec from {url}: {e}") from e


_SENSITIVE_KEYS = {
    "BW_CLIENT_SECRET",
    "BW_ACCESS_TOKEN",
    "_authenticated_servers_loaded",
}


def _safe_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return config with sensitive values redacted."""
    return {k: ("***" if k in _SENSITIVE_KEYS else v) for k, v in config.items()}


def add_resources(mcp: FastMCP, config: Dict[str, Any]) -> FastMCP:
    """Add configuration and other resources to the MCP server."""
    config_resource = FunctionResource(
        name="Bandwidth API Configuration",
        description="Shows which credentials, application IDs, and account ID are configured. Sensitive values are redacted.",
        tags={"bandwidth", "config"},
        uri="resource://config",
        mime_type="application/json",
        fn=lambda: _safe_config(config),
    )

    mcp.add_resource(config_resource)

    for resource in get_bandwidth_resources():
        try:
            mcp.add_resource(resource)
        except Exception as e:
            print(f"Warning: Failed to import resource {resource.name}: {e}")

    return mcp
