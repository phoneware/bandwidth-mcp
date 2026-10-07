"""Meta-tools providing full-registry API discovery and invocation.

Provides:
  - search_api: Keyword search over all 430+ operations across vendored specs.
  - call_api: Invoke any operation in the registry with uniform gating:
      * Every write operation requires a confirm token (confirm='CONFIRM').
      * Destructive operations (disconnect, delete, cancel) additionally require
        in-band MCP elicitation, failing closed when the client lacks support.
      * Numbers operations route through the spec-driven XML adapter.
      * JSON operations route through the OpenAPI HTTP dispatcher.
      * Successful calls are recorded for per-user tool promotion.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional
import httpx

from fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations

from registry import get_registry, RegistryOperation
from xml_adapter import execute_numbers_operation
from promotion import record_call_api_invocation, get_current_user
from tools.discovery import _resolve_account
from safety import (
    check_confirmation,
    confirm_token_for,
    elicit_destructive_confirmation,
    validate_and_quote_path_param,
    validate_declared_args,
)
_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)


def _summarize_args(args: Dict[str, Any]) -> str:
    """Compact summary of arguments for confirmation prompts."""
    if not args:
        return "(no arguments)"
    parts = []
    for k, v in args.items():
        if k in ("confirm", "confirm_token"):
            continue
        v_str = str(v)
        if len(v_str) > 40:
            v_str = v_str[:37] + "..."
        parts.append(f"{k}={v_str}")
        if len(parts) >= 5:
            parts.append("...")
            break
    return ", ".join(parts) if parts else "(no arguments)"

# Argument summary for confirmation messages

async def _dispatch_json_operation(
    op: RegistryOperation,
    args: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute a JSON-based OpenAPI operation (voice, messaging, insights, lookup, etc.)."""
    # 0. Validate declared arguments
    arg_err = validate_declared_args(op, args)
    if arg_err is not None:
        return arg_err

    token = config.get("BW_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("Not authenticated with Bandwidth.")

    # 1. Fill path parameters
    path = op.path
    path_param_names = [p.name for p in op.parameters if p.location == "path"]
    query_param_names = [p.name for p in op.parameters if p.location == "query"]

    args_normalized: Dict[str, Any] = {}
    for k, v in args.items():
        args_normalized[k] = v
        args_normalized[k.lower()] = v
        args_normalized[k.lower().replace("_", "")] = v

    used_keys = set()
    for pname in path_param_names:
        placeholder = f"{{{pname}}}"
        if placeholder not in path:
            continue
        val = None
        if pname == "accountId":
            acct_val = (
                args_normalized.get("accountid")
                or args_normalized.get("account_id")
                or ""
            )
            val = _resolve_account(config, acct_val)
            used_keys.update(["accountid", "account_id", "accountId"])
        else:
            val = (
                args.get(pname)
                or args_normalized.get(pname.lower())
                or args_normalized.get(pname.lower().replace("_", ""))
            )
            used_keys.add(pname)
            used_keys.add(pname.lower())
            used_keys.add(pname.lower().replace("_", ""))

        if val is None:
            raise ValueError(
                f"Missing required path parameter '{pname}' for operation '{op.name}'"
            )
        encoded_val = validate_and_quote_path_param(pname, val)
        path = path.replace(placeholder, encoded_val)

    base_url = op.base_url.rstrip("/")
    rel_path = path.lstrip("/")
    full_url = f"{base_url}/{rel_path}"

    # 2. Collect query parameters
    query_params: Dict[str, Any] = {}
    for qname in query_param_names:
        if qname in args:
            query_params[qname] = args[qname]
            used_keys.add(qname)
        elif qname.lower() in args_normalized:
            query_params[qname] = args_normalized[qname.lower()]
            used_keys.add(qname.lower())

    # 3. Request body for write methods
    body = None
    raw_content = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if op.method in ("POST", "PUT", "PATCH"):
        body_data = {
            k: v
            for k, v in args.items()
            if k not in used_keys
            and k.lower() not in ("confirm", "confirm_token", "account_id", "accountid")
        }
        is_xml_req = (
            op.request_body_content_type == "application/xml"
            or "xml" in (op.request_body_content_type or "").lower()
        )
        if is_xml_req:
            headers["Content-Type"] = "application/xml"
            xml_str = ""
            for key in ("bxml", "xml", "body", "content"):
                if key in body_data and isinstance(body_data[key], str):
                    xml_str = body_data[key]
                    break
            if not xml_str and body_data:
                for val in body_data.values():
                    if isinstance(val, str) and (val.strip().startswith("<") or len(body_data) == 1):
                        xml_str = val
                        break
            raw_content = xml_str.encode("utf-8") if isinstance(xml_str, str) else xml_str
        else:
            headers["Content-Type"] = "application/json"
            if body_data:
                body = body_data

    # 4. Dispatch request
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        resp = await client.request(
            method=op.method,
            url=full_url,
            params=query_params or None,
            json=body if raw_content is None else None,
            content=raw_content,
            headers=headers,
        )
    # 5. Format response
    result: Dict[str, Any] = {
        "status_code": resp.status_code,
    }
    loc = resp.headers.get("Location")
    if loc:
        result["Location"] = loc
        result["id"] = loc.rstrip("/").split("/")[-1]

    text = resp.text.strip()
    if not text:
        result["empty"] = True
        return result

    try:
        result["data"] = resp.json()
    except Exception:
        result["raw"] = text

    if resp.status_code >= 400:
        result["error"] = True

    return result


async def _dispatch_operation(
    op: RegistryOperation,
    args: Dict[str, Any],
    config: Dict[str, Any],
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """Uniform dispatch for any operation with confirm and elicitation gates."""
    # Gate 1: Write confirmation token (per operation)
    if op.is_write:
        conf_err = check_confirmation(op.name, args.get("confirm"))
        if conf_err is not None:
            return conf_err

    # Gate 2: Destructive elicitation prompt
    if op.is_destructive:
        summary = _summarize_args(args)
        elicit_err = await elicit_destructive_confirmation(op.name, summary, ctx)
        if elicit_err is not None:
            return elicit_err
    if op.is_xml:
        return await execute_numbers_operation(op, args, config)
    return await _dispatch_json_operation(op, args, config)


def register_meta_tools(mcp: FastMCP, config: Dict[str, Any]) -> None:
    """Register search_api and call_api escape-hatch tools."""
    reg = get_registry()

    @mcp.tool(
        name="search_api",
        title="Search API Registry",
        description=(
            "Search the full Bandwidth API registry across all vendored specs (numbers, voice, "
            "messaging, insights, lookup, end-user-management, toll-free-verification, multi-factor-auth). "
            "Returns matching tool names, descriptions, and HTTP methods. "
            "Use this when curated tools do not cover what you need, then invoke the discovered tool with call_api."
        ),
        annotations=_READ,
    )
    async def search_api(query: str, limit: int = 15) -> Dict[str, Any]:
        matches = reg.search(query, limit=limit)
        return {
            "query": query,
            "total_matches": len(matches),
            "matches": matches,
        }

    @mcp.tool(
        name="call_api",
        title="Call API Operation",
        description=(
            "Invoke any operation from the full Bandwidth API registry by name. Use after search_api "
            "discovers a tool not included in the curated set. Every write operation requires confirm='CONFIRM'. "
            "Destructive operations (disconnect, delete, cancel) additionally require MCP elicitation confirmation."
        ),
        annotations=_WRITE,
    )
    async def call_api(
        tool_name: str, args: Dict[str, Any] = {}, ctx: Context = None
    ) -> Dict[str, Any]:
        op = reg.get_operation(tool_name)
        if not op:
            return {
                "error": f"Tool '{tool_name}' is not registered in the API registry. Use search_api to discover correct tool names.",
                "status_code": 404,
            }

        res = await _dispatch_operation(op, args, config, ctx)

        # Record invocation for per-user tool promotion on success
        if (
            not res.get("error")
            and not res.get("cancelled")
            and not res.get("requires_confirmation")
        ):
            user = get_current_user()
            await record_call_api_invocation(
                user, op.name, mcp_instance=mcp, config=config
            )

        return res
