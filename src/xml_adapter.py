"""Spec-driven XML adapter for the Bandwidth Numbers / Dashboard API.

Turns JSON arguments into XML request bodies using the spec schemas and converts
XML responses into clean JSON structures using the generic _xml_to_data parser.
Allows every Numbers operation in the OpenAPI spec to be invoked through call_api.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from xml.etree.ElementTree import Element, SubElement, fromstring, tostring
import httpx

from tools.discovery import _resolve_account
from tools.numbers import _xml_to_data
from urls import dashboard_api_base
from registry import RegistryOperation


def _resolve_root_tag(op: RegistryOperation, body_data: Any) -> str:
    """Determine the XML root tag from schema metadata or operation name."""
    schema = op.request_body_schema or {}
    # 1. Schema xml metadata
    xml_meta = schema.get("xml")
    if isinstance(xml_meta, dict) and xml_meta.get("name"):
        return xml_meta["name"]

    # 2. If schema has a single property with an object schema, that property is often the root wrapper
    props = schema.get("properties", {})
    if len(props) == 1:
        single_prop = next(iter(props.keys()))
        if single_prop[0].isupper():
            return single_prop

    # 3. Strip verb prefixes from schema title or bare operation ID
    candidate = schema.get("title") or op.bare_name
    for prefix in ("Create", "Update", "Patch", "Put", "Post"):
        if candidate.startswith(prefix) and len(candidate) > len(prefix):
            return candidate[len(prefix) :]

    if candidate and candidate.isalnum():
        return candidate

    return "Request"


def dict_to_xml(
    tag: str, data: Any, schema: Optional[Dict[str, Any]] = None
) -> Element:
    """Convert JSON-compatible data into an ElementTree Element.

    Handles:
      - Attributes (keys starting with '@')
      - Text content (keys '#text' or 'text')
      - Lists: wrapped or unwrapped repeating elements
      - Nested dicts: sub-elements
      - Primitive scalars: text values
    """
    el = Element(tag)
    if not isinstance(data, dict):
        if data is not None:
            el.text = str(data)
        return el

    props_schema = (
        schema.get("properties", {}) if schema and isinstance(schema, dict) else {}
    )

    for k, v in data.items():
        if v is None:
            continue
        if k.startswith("@"):
            el.set(k[1:], str(v))
        elif k in ("#text", "text") and not isinstance(v, (dict, list)):
            el.text = str(v)
        elif isinstance(v, list):
            prop_meta = (
                props_schema.get(k, {}) if isinstance(props_schema, dict) else {}
            )
            xml_meta = prop_meta.get("xml", {}) if isinstance(prop_meta, dict) else {}
            is_wrapped = xml_meta.get("wrapped", False)
            item_name = xml_meta.get("name")

            if is_wrapped:
                wrapper = SubElement(el, k)
                sub_tag = item_name or _singularize(k)
                for item in v:
                    if isinstance(item, dict):
                        wrapper.append(
                            dict_to_xml(sub_tag, item, prop_meta.get("items"))
                        )
                    else:
                        SubElement(wrapper, sub_tag).text = str(item)
            else:
                sub_tag = item_name or k
                for item in v:
                    if isinstance(item, dict):
                        el.append(dict_to_xml(sub_tag, item, prop_meta.get("items")))
                    else:
                        SubElement(el, sub_tag).text = str(item)
        elif isinstance(v, dict):
            child_schema = (
                props_schema.get(k) if isinstance(props_schema, dict) else None
            )
            el.append(dict_to_xml(k, v, child_schema))
        else:
            SubElement(el, k).text = str(v)

    return el


def _singularize(name: str) -> str:
    """Simple singularization heuristic for XML list item elements."""
    if name.endswith("List"):
        base = name[:-4]
        return base
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("ses"):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name + "Item"


async def execute_numbers_operation(
    op: RegistryOperation,
    args: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute any Numbers/Dashboard operation via the spec-driven XML adapter."""
    token = config.get("BW_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("Not authenticated with Bandwidth.")

    # 1. Resolve path parameters
    path = op.path
    path_param_names = [p.name for p in op.parameters if p.location == "path"]
    query_param_names = [p.name for p in op.parameters if p.location == "query"]

    # Normalize args lookup (case-insensitive and snake_case matching)
    args_normalized: Dict[str, Any] = {}
    for k, v in args.items():
        args_normalized[k] = v
        args_normalized[k.lower()] = v
        args_normalized[k.lower().replace("_", "")] = v

    used_keys = set()

    # Fill path parameters
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
        path = path.replace(placeholder, str(val))

    # Clean leading slash if any to append to base
    rel_path = path.lstrip("/")
    base_url = dashboard_api_base().rstrip("/")
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

    # 3. Request body
    content = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/xml",
    }

    if op.method in ("POST", "PUT", "PATCH"):
        # Remaining args form body data
        body_data = {
            k: v
            for k, v in args.items()
            if k not in used_keys
            and k.lower() not in ("confirm", "confirm_token", "account_id", "accountid")
        }

        # Handle file upload or json body exception
        if op.request_body_content_type == "application/json":
            headers["Content-Type"] = "application/json"
            content = httpx._types.json_dumps(body_data)
        elif body_data:
            headers["Content-Type"] = "application/xml; charset=utf-8"
            root_tag = _resolve_root_tag(op, body_data)
            # Check if root tag is already wrapped in body_data
            for k in list(body_data.keys()):
                if k.lower() == root_tag.lower() and isinstance(body_data[k], dict):
                    body_data = body_data[k]
                    break

            xml_element = dict_to_xml(root_tag, body_data, op.request_body_schema)
            content = tostring(xml_element, encoding="unicode")

    # 4. Dispatch HTTP request
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        resp = await client.request(
            method=op.method,
            url=full_url,
            params=query_params or None,
            content=content,
            headers=headers,
        )

    # 5. Format response
    result: Dict[str, Any] = {
        "status_code": resp.status_code,
    }

    # Extract Location header
    loc = resp.headers.get("Location")
    if loc:
        result["Location"] = loc
        result["id"] = loc.rstrip("/").split("/")[-1]

    text = resp.text.strip()
    if not text:
        result["empty"] = True
        return result

    # Parse XML response
    try:
        root = fromstring(text)
        result[root.tag] = _xml_to_data(root)
    except Exception:
        # Fallback if text is not valid XML (e.g. plain text or JSON error)
        try:
            result["data"] = resp.json()
        except Exception:
            result["raw"] = text

    if resp.status_code >= 400:
        result["error"] = True

    return result
