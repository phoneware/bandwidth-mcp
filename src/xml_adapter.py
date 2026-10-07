"""Spec-driven XML adapter for the Bandwidth Numbers / Dashboard API.

Turns JSON arguments into XML request bodies using the spec schemas and converts
XML responses into clean JSON structures using the generic _xml_to_data parser.
Allows every Numbers operation in the OpenAPI spec to be invoked through call_api.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, Optional
from xml.etree.ElementTree import Element, SubElement, fromstring, tostring
import httpx
from tools.discovery import _resolve_account
from tools.numbers import _xml_to_data
from urls import dashboard_api_base
from registry import RegistryOperation
from safety import validate_and_quote_path_param, validate_declared_args


def _resolve_root_tag(op: RegistryOperation, body_data: Any) -> str:
    """Determine the XML root tag from schema metadata or operation name."""
    schema = op.request_body_schema or {}

    # 0. Check oneOf / anyOf composed schemas
    for combo in ("oneOf", "anyOf"):
        variants = schema.get(combo)
        if isinstance(variants, list):
            if isinstance(body_data, dict):
                for variant in variants:
                    if isinstance(variant, dict):
                        props = variant.get("properties", {})
                        for pk in props:
                            if any(k.lower() == pk.lower() for k in body_data.keys()):
                                return pk
            variant_props = []
            for variant in variants:
                if isinstance(variant, dict):
                    props = variant.get("properties", {})
                    if len(props) == 1:
                        variant_props.append(next(iter(props.keys())))
            if variant_props and all(p == variant_props[0] for p in variant_props):
                return variant_props[0]

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

    # 3. If body_data has a single top-level uppercase key matching schema property or title
    if isinstance(body_data, dict) and len(body_data) == 1:
        single_key = next(iter(body_data.keys()))
        if single_key[0].isupper() and (single_key in props or single_key == schema.get("title")):
            return single_key

    # 4. Strip verb prefixes from schema title or bare operation ID
    candidate = schema.get("title") or op.bare_name
    for prefix in ("Create", "Update", "Patch", "Put", "Post"):
        if candidate.startswith(prefix) and len(candidate) > len(prefix):
            return candidate[len(prefix) :]

    if candidate and candidate.isalnum():
        return candidate

    return "Request"


def _find_child_schema(schema: Optional[Dict[str, Any]], root_tag: str) -> Optional[Dict[str, Any]]:
    if not schema or not isinstance(schema, dict):
        return schema
    props = schema.get("properties", {})
    for pk, pv in props.items():
        if pk.lower() == root_tag.lower():
            return pv
    for combo in ("oneOf", "anyOf", "allOf"):
        variants = schema.get(combo)
        if isinstance(variants, list):
            for variant in variants:
                if isinstance(variant, dict):
                    v_props = variant.get("properties", {})
                    for pk, pv in v_props.items():
                        if pk.lower() == root_tag.lower():
                            return pv
    return schema


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
            items_schema = prop_meta.get("items", {}) if isinstance(prop_meta, dict) else {}
            item_name = xml_meta.get("name")
            if not item_name and isinstance(items_schema, dict):
                item_name = items_schema.get("xml", {}).get("name")
                if not item_name and "properties" in items_schema and len(items_schema["properties"]) == 1:
                    item_name = next(iter(items_schema["properties"].keys()))
                if not item_name and items_schema.get("title"):
                    item_name = items_schema["title"]

            sub_tag = item_name or _singularize(k)

            # In Bandwidth XML, collections are wrapped containers unless explicitly marked unwrapped.
            is_wrapped = False
            if xml_meta.get("wrapped") is True:
                is_wrapped = True
            elif xml_meta.get("wrapped") is False:
                is_wrapped = False
            elif k.endswith("List") or k.startswith("ListOf"):
                is_wrapped = True
            elif k.endswith("s") and not k.endswith("ss"):
                is_wrapped = True

            if is_wrapped:
                wrapper = SubElement(el, k)
                for item in v:
                    if isinstance(item, dict):
                        if len(item) == 1 and sub_tag in item:
                            val = item[sub_tag]
                            if isinstance(val, (dict, list)):
                                wrapper.append(dict_to_xml(sub_tag, val, items_schema))
                            else:
                                SubElement(wrapper, sub_tag).text = str(val)
                        else:
                            wrapper.append(dict_to_xml(sub_tag, item, items_schema))
                    else:
                        SubElement(wrapper, sub_tag).text = str(item)
            else:
                for item in v:
                    if isinstance(item, dict):
                        el.append(dict_to_xml(sub_tag, item, items_schema))
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
    if name.startswith("ListOf"):
        base = name[6:]
        return _singularize(base)
    if name.endswith("List"):
        base = name[:-4]
        return base
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("ses"):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name

async def execute_numbers_operation(
    op: RegistryOperation,
    args: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute any Numbers/Dashboard operation via the spec-driven XML adapter."""
    # 0. Validate declared arguments
    arg_err = validate_declared_args(op, args)
    if arg_err is not None:
        return arg_err

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
        encoded_val = validate_and_quote_path_param(pname, val)
        path = path.replace(placeholder, encoded_val)

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

        is_binary_upload = (
            (op.request_body_schema and op.request_body_schema.get("format") == "binary")
            or op.request_body_content_type in ("*/*", "application/octet-stream")
            or any(k in args for k in ("file", "file_base64"))
        )
        if is_binary_upload:
            raw_file = (
                args.get("file")
                or args.get("file_base64")
                or args.get("content")
                or args.get("data")
            )
            if raw_file is not None:
                if isinstance(raw_file, str):
                    try:
                        content = base64.b64decode(raw_file)
                    except Exception:
                        content = raw_file.encode("utf-8")
                elif isinstance(raw_file, (bytes, bytearray)):
                    content = bytes(raw_file)
                else:
                    content = str(raw_file).encode("utf-8")

                doc_type = str(args.get("documentType") or args.get("document_type") or "").upper()
                filename = str(args.get("filename") or "")
                if "content_type" in args:
                    upload_ct = args["content_type"]
                elif "media_type" in args:
                    upload_ct = args["media_type"]
                elif doc_type == "LOA" or filename.lower().endswith(".pdf"):
                    upload_ct = "application/pdf"
                else:
                    upload_ct = "application/octet-stream"
                headers["Content-Type"] = upload_ct
        elif op.request_body_content_type == "application/json":
            headers["Content-Type"] = "application/json"
            content = json.dumps(body_data)
        elif body_data:
            headers["Content-Type"] = "application/xml; charset=utf-8"
            root_tag = _resolve_root_tag(op, body_data)
            child_schema = _find_child_schema(op.request_body_schema, root_tag)
            # Check if root tag is already wrapped in body_data
            for k in list(body_data.keys()):
                if k.lower() == root_tag.lower() and isinstance(body_data[k], dict):
                    body_data = body_data[k]
                    break

            xml_element = dict_to_xml(root_tag, body_data, child_schema)
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

    if resp.status_code < 400:
        content_type = resp.headers.get("Content-Type", "").lower()
        content_disp = resp.headers.get("Content-Disposition", "").lower()
        is_binary_resp = (
            "application/octet-stream" in content_type
            or "application/pdf" in content_type
            or "image/" in content_type
            or "zip" in content_type
            or "audio/" in content_type
            or "attachment" in content_disp
        )
        if is_binary_resp:
            result["data_base64"] = base64.b64encode(resp.content).decode("ascii")
            result["content_type"] = resp.headers.get("Content-Type", "application/octet-stream")
            result["size_bytes"] = len(resp.content)
            disp_header = resp.headers.get("Content-Disposition", "")
            if "filename=" in disp_header:
                parts = disp_header.split("filename=")
                if len(parts) > 1:
                    result["filename"] = parts[1].strip('"\' ;')
            return result

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
