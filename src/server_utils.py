import copy
import hashlib
import warnings
import yaml
import httpx

from pathlib import Path
from fastmcp import FastMCP
from fastmcp.resources import FunctionResource
from fastmcp.server.providers.openapi import MCPType
from fastmcp.utilities.openapi import HTTPRoute
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

    all_tools = await mcp.list_tools()
    all_resources = await mcp.list_resources()

    tool_names = [tool.name for tool in all_tools]
    resource_names = [resource.name for resource in all_resources]

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
        # Excluded takes priority over enabled, but BOTH apply. (The original
        # returned early when excluded_tools was set, which silently ignored
        # the enabled list: profile + exclusions together loaded every tool
        # the specs expose except the excluded ones.)
        if excluded_tools and route.operation_id in excluded_tools:
            return MCPType.EXCLUDE
        if enabled_tools and route.operation_id not in enabled_tools:
            return MCPType.EXCLUDE
        return mcp_type

    return route_map_fn


def _fix_malformed_refs(obj: Any) -> Any:
    """Fix malformed $ref strings in OpenAPI specs.

    Bandwidth's numbers spec has $refs as bare strings like:
      "$ref:'#/components/schemas/Foo'"
    instead of proper dicts like:
      {"$ref": "#/components/schemas/Foo"}
    """
    if isinstance(obj, str) and obj.startswith("$ref:"):
        # Extract the ref path from malformed string
        ref_path = obj.split(":", 1)[1].strip("'\"")
        return {"$ref": ref_path}
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            obj[k] = _fix_malformed_refs(v)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            obj[i] = _fix_malformed_refs(item)
    return obj


def _fix_response_descriptions(obj: Any) -> Any:
    """Fix responses missing 'description' field (required by OpenAPI 3.x).

    Bandwidth's numbers spec has responses like:
      204: {content: {description: "No Content"}}
    instead of:
      204: {description: "No Content"}
    """
    if isinstance(obj, dict) and "responses" in obj:
        for code, response in obj["responses"].items():
            if isinstance(response, dict) and "description" not in response:
                # Try to extract description from content
                if "content" in response and isinstance(response["content"], dict):
                    desc = response["content"].get("description", str(code))
                    if isinstance(desc, str):
                        response["description"] = desc
                        del response["content"]
                        continue
                response["description"] = str(code)
    if isinstance(obj, dict):
        for v in obj.values():
            _fix_response_descriptions(v)
    elif isinstance(obj, list):
        for item in obj:
            _fix_response_descriptions(item)
    return obj


def _fix_misplaced_allof(obj: Any) -> Any:
    """Fix allOf misplaced inside 'properties' instead of at schema level.

    Bandwidth's numbers spec has schemas like:
      {properties: {allOf: [...], type: 'object'}}
    where allOf should be a sibling of properties, not a child:
      {allOf: [...], type: 'object'}
    """
    if isinstance(obj, dict):
        if "properties" in obj and isinstance(obj["properties"], dict):
            props = obj["properties"]
            if "allOf" in props and isinstance(props["allOf"], list):
                # Hoist allOf from properties to the schema level
                obj["allOf"] = props.pop("allOf")
                # Move type up too if it's in properties
                if "type" in props and not any(
                    k not in ("allOf", "type", "xml") for k in props
                ):
                    obj.setdefault("type", props.pop("type", "object"))
                    if "xml" in props:
                        obj.setdefault("xml", props.pop("xml"))
                    if not props:
                        del obj["properties"]
        for v in obj.values():
            _fix_misplaced_allof(v)
    elif isinstance(obj, list):
        for item in obj:
            _fix_misplaced_allof(item)
    return obj


def _clean_openapi_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Clean and patch OpenAPI spec for compatibility.

    - Remove callbacks, 4xx/5xx responses, x- fields
    - Fix malformed $ref strings (bare strings → proper dicts)
    - Fix responses missing required 'description' field
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

    _clean(cleaned_spec)
    _fix_malformed_refs(cleaned_spec)
    _fix_response_descriptions(cleaned_spec)
    _fix_misplaced_allof(cleaned_spec)
    _patch_phone_number_lookup(cleaned_spec)

    return cleaned_spec


def _patch_phone_number_lookup(spec: Dict[str, Any]) -> None:
    """The upstream phone-number-lookup-v2 spec ships with empty request body
    schemas for both createSyncLookup and createAsyncBulkLookup. Without a
    schema, FastMCP from_openapi can't surface the body field to the agent,
    so the lookup tools become unusable.

    Inject the real shape (confirmed against the live API on stage). The body
    field is `phoneNumbers`, not `tns` as some older docs imply.
    """
    paths = spec.get("paths", {})
    targets = (
        "/accounts/{accountId}/phoneNumberLookup",
        "/accounts/{accountId}/phoneNumberLookup/bulk",
    )
    body_schema = {
        "type": "object",
        "required": ["phoneNumbers"],
        "properties": {
            "phoneNumbers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Phone numbers in E.164 format to look up (e.g. \"+19195551234\").",
            }
        },
    }
    for path in targets:
        op = paths.get(path, {}).get("post")
        if not isinstance(op, dict):
            continue
        rb = op.setdefault("requestBody", {"required": True, "content": {}})
        content = rb.setdefault("content", {})
        media = content.setdefault("application/json", {})
        # Only inject if the upstream schema is missing or empty.
        if not media.get("schema"):
            media["schema"] = body_schema


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
