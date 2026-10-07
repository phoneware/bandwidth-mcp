"""Safety gates and parameter validation for Bandwidth MCP.

Provides:
  - Per-operation confirm token generation and verification.
  - In-band MCP elicitation prompts for destructive operations.
  - Path traversal defense: validates that path parameters do not contain '/' or '..'
    and applies strict URL-encoding (quote with safe="").
  - Undeclared argument rejection: ensures no unexpected keys are silently folded
    into requests.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote

from fastmcp import Context
from registry import RegistryOperation


def confirm_token_for(name: str) -> str:
    """Derive the required confirmation token for a tool or operation name.
    
    Example:
      'numbers.CreateSite' -> 'CREATESITE'
      'orderPhoneNumbers' -> 'ORDERPHONENUMBERS'
    """
    bare = name.split(".")[-1]
    # Keep alphanumeric characters only, uppercase
    token = re.sub(r"[^a-zA-Z0-9]", "", bare).upper()
    return token or "CONFIRM"


def check_confirmation(name: str, confirm_val: Any) -> Optional[Dict[str, Any]]:
    """Verify that the supplied confirm token matches the expected per-operation token.
    
    Returns an error refusal dict if missing or invalid, or None if confirmed.
    """
    required_token = confirm_token_for(name)
    if not isinstance(confirm_val, str):
        return {
            "error": (
                f"Write operation '{name}' requires confirmation. "
                f"Pass confirm='{required_token}' to proceed."
            ),
            "requires_confirmation": True,
            "confirm_token": required_token,
            "tool_name": name,
        }

    supplied = confirm_val.strip().upper()
    # Accept bare token or full namespaced token
    valid_tokens = {
        required_token,
        name.upper(),
        name.replace(".", "_").upper(),
    }

    if supplied not in valid_tokens:
        return {
            "error": (
                f"Write operation '{name}' requires confirmation with token '{required_token}' "
                f"(received {confirm_val!r}). Pass confirm='{required_token}' to proceed."
            ),
            "requires_confirmation": True,
            "confirm_token": required_token,
            "tool_name": name,
        }

    return None


async def elicit_destructive_confirmation(
    name: str,
    summary: str,
    ctx: Optional[Context],
) -> Optional[Dict[str, Any]]:
    """Elicit confirmation for a destructive operation via MCP elicitation.
    
    Returns an error dict on decline/cancel/missing capability, or None if approved.
    """
    message = (
        f"This tool can permanently change or delete carrier data.\n\n"
        f"Tool: {name}\n"
        f"Arguments: {summary}\n\n"
        f"Proceed with execution?"
    )

    if ctx is not None and hasattr(ctx, "elicit"):
        try:
            elicit_res = await ctx.elicit(
                message=message,
                response_type=bool,
                response_title="Confirm Execution",
                response_description="Enter 'yes' / True to confirm destructive operation.",
            )
            val = getattr(elicit_res, "value", None)
            if val is None:
                val = getattr(elicit_res, "data", None)
            if val is True:
                return None
            return {
                "error": f"User declined or cancelled destructive operation '{name}'.",
                "cancelled": True,
            }
        except Exception:
            fallback = os.environ.get("MCP_CONFIRM_FALLBACK", "fail").lower()
            if fallback == "allow":
                return None
            return {
                "error": (
                    f"Tool '{name}' is destructive and the connected client does not support confirmation prompts. "
                    f"Set MCP_CONFIRM_FALLBACK=allow to bypass on such clients, or use a client that supports MCP elicitation."
                ),
                "elicitation_unsupported": True,
            }
    else:
        fallback = os.environ.get("MCP_CONFIRM_FALLBACK", "fail").lower()
        if fallback == "allow":
            return None
        return {
            "error": (
                f"Tool '{name}' is destructive and no elicitation context was provided. "
                f"Set MCP_CONFIRM_FALLBACK=allow to allow unprompted destructive execution in automated harnesses."
            ),
            "elicitation_unsupported": True,
        }


def validate_and_quote_path_param(pname: str, val: Any) -> str:
    """Validate and strictly URL-encode a path parameter value.
    
    Rejects any value containing '/' or '..' to prevent path traversal.
    """
    val_str = str(val)
    if "/" in val_str or ".." in val_str:
        raise ValueError(f"Path parameter '{pname}' cannot contain '/' or '..': {val_str!r}")
    return quote(val_str, safe="")


def _to_snake_case(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return s.lower()

def _extract_schema_properties(schema: Dict[str, Any], declared: Set[str], depth: int = 0) -> None:
    if not isinstance(schema, dict) or depth > 8:
        return

    xml_meta = schema.get("xml")
    if isinstance(xml_meta, dict) and xml_meta.get("name"):
        tag = xml_meta["name"]
        declared.add(tag)
        declared.add(tag.lower())

    title = schema.get("title")
    if title:
        declared.add(title)
        declared.add(title.lower())

    props = schema.get("properties")
    if isinstance(props, dict):
        for prop_name, prop_val in props.items():
            declared.add(prop_name)
            declared.add(prop_name.lower())
            declared.add(_to_snake_case(prop_name))
            if isinstance(prop_val, dict):
                _extract_schema_properties(prop_val, declared, depth + 1)

    for combo in ("oneOf", "anyOf", "allOf"):
        variants = schema.get(combo)
        if isinstance(variants, list):
            for variant in variants:
                if isinstance(variant, dict):
                    _extract_schema_properties(variant, declared, depth + 1)


def get_declared_param_names(op: RegistryOperation) -> Set[str]:
    """Collect all declared parameter and schema property names for an operation."""
    declared = set()

    for p in op.parameters:
        declared.add(p.name)
        declared.add(p.name.lower())
        declared.add(_to_snake_case(p.name))
        if p.name == "accountId":
            declared.update(["account_id", "accountid", "accountId"])

    # System parameters
    declared.add("confirm")

    # Request body properties
    schema = op.request_body_schema or {}
    _extract_schema_properties(schema, declared)

    # Raw XML string body (e.g. voice.updateCallBxml, voice.updateConferenceBxml)
    ct = (op.request_body_content_type or "").lower()
    if "xml" in ct or "bxml" in op.bare_name.lower():
        declared.update({"bxml", "xml", "body", "content", "document"})

    # Binary upload body (e.g. numbers.UploadPortinLoaFile)
    if schema.get("format") == "binary" or ct in ("*/*", "application/octet-stream", "application/pdf"):
        declared.update({"file", "file_base64", "content", "data", "filename", "content_type", "media_type", "documenttype", "document_type"})

    return declared


def validate_declared_args(op: RegistryOperation, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate that every key in args is a declared parameter or body property.
    
    Returns an error dict naming the undeclared arguments if found, None if valid.
    """
    if not isinstance(args, dict):
        return None

    declared = get_declared_param_names(op)
    undeclared = []

    for k in args:
        k_norm = k.lower().replace("_", "")
        # Check direct or normalized match
        is_known = (
            k in declared
            or k.lower() in declared
            or _to_snake_case(k) in declared
            or any(d.lower().replace("_", "") == k_norm for d in declared)
        )
        if not is_known:
            undeclared.append(k)

    if undeclared:
        clean_declared = sorted({p for p in declared if not p.startswith("@") and p != p.lower() or "_" in p})
        if not clean_declared:
            clean_declared = sorted(declared)
        return {
            "error": (
                f"Undeclared argument(s) {undeclared} for operation '{op.name}'. "
                f"Declared parameters: {clean_declared}"
            ),
            "status_code": 400,
            "undeclared": undeclared,
            "declared": clean_declared,
        }

    return None
