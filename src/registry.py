"""Unified OpenAPI registry for all Bandwidth APIs.

Vendors and builds a unified registry across all published Bandwidth specs:
  - numbers (~340 operations, XML-based)
  - voice (28 operations)
  - messaging (7 operations)
  - insights (21 operations)
  - lookup (phone-number-lookup-v2, 3 operations)
  - end-user-management (32 operations)
  - toll-free-verification (9 operations)
  - multi-factor-auth (3 operations)

Every operation is namespaced (<spec>.<operationId>, e.g. insights.listCalls vs
voice.listCalls) so collisions never drop an operation. Credential and
infrastructure management operations are stripped at generation time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import re
import yaml

from mcp.types import ToolAnnotations
from urls import (
    swap_host,
    dashboard_api_base,
    api_base,
    voice_base,
    messaging_base,
    insights_base,
    mfa_base,
)

_SPECS_DIR = Path(__file__).parent / "specs"

# Spec file mapping: key -> filename
SPEC_FILES: Dict[str, str] = {
    "numbers": "numbers.yml",
    "voice": "voice.yml",
    "messaging": "messaging.yml",
    "insights": "insights.yml",
    "lookup": "phone-number-lookup-v2.yml",
    "end-user-management": "end-user-management.yml",
    "toll-free-verification": "toll-free-verification.yml",
    "multi-factor-auth": "multi-factor-auth.yml",
}

# Spec aliases for flexible invocation
SPEC_ALIASES: Dict[str, str] = {
    "phone-number-lookup": "lookup",
    "phone-number-lookup-v2": "lookup",
    "mfa": "multi-factor-auth",
}

# Written exclusion list: credential and infrastructure operations stripped at generation time.
# These endpoints manage raw SIP trunk secrets, carrier passwords, or registration flows
# that must never be exposed through the model surface.
EXCLUDED_OPERATIONS: set[str] = {
    # SIP trunk credentials (numbers spec)
    "numbers.ListSipCredentialsOnRealm",
    "numbers.CreateSipCredentialsOnRealm",
    "numbers.DeleteSipCredentialOnRealm",
    "numbers.RetrieveSipCredentialOnRealm",
    "numbers.UpdateSipCredentialOnRealm",
    "numbers.RetrieveSipCredentials",
    "numbers.CreateSipCredentials",
    "numbers.DeleteSipCredentials",
    "numbers.RetrieveSipCredential",
    "numbers.UpdateSipCredentials",
    # Onboarding and stdio credential tools
    "build-registration.createRegistration",
    "build.createRegistration",
    "credentials.setCredentials",
    "credentials.clearCredentials",
}

_DESTRUCTIVE_KEYWORDS = ("delete", "disconnect", "cancel", "terminate", "remove")


@dataclass
class ParameterDef:
    name: str
    location: str  # "path", "query", "header", "cookie"
    required: bool
    type: Optional[str]
    description: Optional[str]
    schema: Optional[Dict[str, Any]] = None


@dataclass
class RegistryOperation:
    name: str  # Canonical namespaced ID: e.g. "insights.listCalls"
    bare_name: str  # Raw operationId from spec: e.g. "listCalls"
    spec_name: str  # Spec namespace: e.g. "insights"
    title: str  # Human-readable title
    description: str  # Detailed description / summary
    method: str  # "GET", "POST", "PUT", "PATCH", "DELETE"
    path: str  # OpenAPI path template: e.g. "/v1/voice/calls"
    base_url: str  # Resolved server base URL
    parameters: List[ParameterDef] = field(default_factory=list)
    request_body_schema: Optional[Dict[str, Any]] = None
    request_body_content_type: str = "application/json"
    responses: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    is_write: bool = False
    is_destructive: bool = False
    is_xml: bool = False
    annotations: ToolAnnotations = field(
        default_factory=lambda: ToolAnnotations(readOnlyHint=True)
    )


def _human_title(name: str) -> str:
    """Format an operationId into a human-readable title."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    s = s.replace("_", " ").replace("-", " ")
    return " ".join(w.capitalize() for w in s.split())


def _resolve_ref(ref: str, root_spec: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve a local #/components/... JSON reference."""
    if not ref.startswith("#/"):
        return {}
    parts = ref.lstrip("#/").split("/")
    cur: Any = root_spec
    for part in parts:
        if not isinstance(cur, dict):
            return {}
        cur = cur.get(part)
        if cur is None:
            return {}
    return cur if isinstance(cur, dict) else {}


def _resolve_schema(
    schema: Optional[Dict[str, Any]], root_spec: Dict[str, Any], depth: int = 0
) -> Optional[Dict[str, Any]]:
    """Deep-resolve $ref pointers within a schema up to a recursion depth."""
    if not schema or not isinstance(schema, dict) or depth > 10:
        return schema
    if "$ref" in schema:
        resolved = _resolve_ref(schema["$ref"], root_spec)
        merged = dict(resolved)
        # Preserve local xml or description overrides
        for k, v in schema.items():
            if k != "$ref":
                merged[k] = v
        return _resolve_schema(merged, root_spec, depth + 1)
    out = dict(schema)
    if "properties" in out and isinstance(out["properties"], dict):
        out["properties"] = {
            pk: _resolve_schema(pv, root_spec, depth + 1)
            for pk, pv in out["properties"].items()
        }
    if "items" in out and isinstance(out["items"], dict):
        out["items"] = _resolve_schema(out["items"], root_spec, depth + 1)
    if "oneOf" in out and isinstance(out["oneOf"], list):
        out["oneOf"] = [_resolve_schema(s, root_spec, depth + 1) for s in out["oneOf"]]
    if "anyOf" in out and isinstance(out["anyOf"], list):
        out["anyOf"] = [_resolve_schema(s, root_spec, depth + 1) for s in out["anyOf"]]
    if "allOf" in out and isinstance(out["allOf"], list):
        out["allOf"] = [_resolve_schema(s, root_spec, depth + 1) for s in out["allOf"]]
    return out


def _resolve_server_url(spec_name: str, servers: List[Dict[str, Any]]) -> str:
    """Resolve the base server URL for an API spec."""
    if spec_name == "numbers":
        return dashboard_api_base()
    if servers and isinstance(servers, list):
        first_url = servers[0].get("url", "")
        if first_url:
            return swap_host(first_url)

    # Fallback mappings based on urls.py
    if spec_name == "voice":
        return voice_base()
    if spec_name == "messaging":
        return messaging_base()
    if spec_name == "insights":
        return f"{insights_base()}/api"
    if spec_name == "multi-factor-auth":
        return mfa_base()
    return api_base()


class ApiRegistry:
    """In-memory registry of all operations across all vendored specs."""

    def __init__(self) -> None:
        self._operations: Dict[str, RegistryOperation] = {}
        self._bare_to_namespaced: Dict[str, List[str]] = {}
        self._specs_data: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def load(self, specs_dir: Path = _SPECS_DIR) -> None:
        if self._loaded:
            return
        self._operations.clear()
        self._bare_to_namespaced.clear()

        for spec_name, filename in SPEC_FILES.items():
            spec_path = specs_dir / filename
            if not spec_path.exists():
                continue
            try:
                spec_data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"Warning: could not load spec {filename}: {e}")
                continue

            if not isinstance(spec_data, dict):
                continue

            self._specs_data[spec_name] = spec_data
            self._load_spec(spec_name, spec_data)

        self._loaded = True

    def _load_spec(self, spec_name: str, spec_data: Dict[str, Any]) -> None:
        servers = spec_data.get("servers", [])
        base_url = _resolve_server_url(spec_name, servers)
        components = spec_data.get("components", {})
        param_defs = components.get("parameters", {})
        paths = spec_data.get("paths", {})

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            path_params = path_item.get("parameters", [])

            for method_str, op in path_item.items():
                method = method_str.upper()
                if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                    continue
                if not isinstance(op, dict):
                    continue

                bare_id = op.get("operationId")
                if not bare_id:
                    continue

                namespaced_name = f"{spec_name}.{bare_id}"
                if namespaced_name in EXCLUDED_OPERATIONS:
                    continue

                summary = (op.get("summary") or "").strip()
                description = (op.get("description") or "").strip()
                full_desc = (
                    f"{summary}\n\n{description}".strip()
                    if summary and description
                    else (summary or description or namespaced_name)
                )
                title = f"{_human_title(bare_id)} ({spec_name.capitalize()})"

                # Aggregate parameters
                combined_params = []
                for p in list(path_params) + list(op.get("parameters", [])):
                    if not isinstance(p, dict):
                        continue
                    if "$ref" in p:
                        ref_name = p["$ref"].split("/")[-1]
                        p = param_defs.get(ref_name, p)
                    pname = p.get("name")
                    if not pname:
                        continue
                    pschema = _resolve_schema(p.get("schema"), spec_data)
                    ptype = pschema.get("type") if isinstance(pschema, dict) else None
                    combined_params.append(
                        ParameterDef(
                            name=pname,
                            location=p.get("in", "query"),
                            required=bool(p.get("required", False)),
                            type=ptype,
                            description=p.get("description"),
                            schema=pschema,
                        )
                    )

                # Request body schema
                rb_schema: Optional[Dict[str, Any]] = None
                rb_content_type = (
                    "application/xml" if spec_name == "numbers" else "application/json"
                )
                rb = op.get("requestBody")
                if isinstance(rb, dict):
                    if "$ref" in rb:
                        rb = _resolve_ref(rb["$ref"], spec_data)
                    content = rb.get("content", {})
                    # Select content type (XML for numbers, JSON otherwise)
                    if "application/xml" in content:
                        rb_content_type = "application/xml"
                        raw_schema = content["application/xml"].get("schema")
                        rb_schema = _resolve_schema(raw_schema, spec_data)
                    elif "application/json" in content:
                        rb_content_type = "application/json"
                        raw_schema = content["application/json"].get("schema")
                        rb_schema = _resolve_schema(raw_schema, spec_data)
                    elif content:
                        first_ct = next(iter(content.keys()))
                        rb_content_type = first_ct
                        raw_schema = content[first_ct].get("schema")
                        rb_schema = _resolve_schema(raw_schema, spec_data)

                # Operation classification
                is_write = method in ("POST", "PUT", "PATCH", "DELETE")
                bare_lower = bare_id.lower()
                is_destructive = is_write and (
                    method == "DELETE"
                    or any(k in bare_lower for k in _DESTRUCTIVE_KEYWORDS)
                    or any(k in path.lower() for k in ("disconnect", "cancel"))
                )
                is_xml = spec_name == "numbers"

                annotations = ToolAnnotations(
                    readOnlyHint=not is_write,
                    destructiveHint=is_destructive,
                    openWorldHint=False,
                )

                reg_op = RegistryOperation(
                    name=namespaced_name,
                    bare_name=bare_id,
                    spec_name=spec_name,
                    title=title,
                    description=full_desc,
                    method=method,
                    path=path,
                    base_url=base_url,
                    parameters=combined_params,
                    request_body_schema=rb_schema,
                    request_body_content_type=rb_content_type,
                    responses=op.get("responses", {}),
                    tags=op.get("tags", []),
                    is_write=is_write,
                    is_destructive=is_destructive,
                    is_xml=is_xml,
                    annotations=annotations,
                )

                # Disambiguate duplicate operationIds within the same spec
                if namespaced_name in self._operations:
                    existing_op = self._operations[namespaced_name]
                    if "{" in path and "}" in path:
                        param_match = re.search(r"\{([^}]+)\}$", path)
                        suffix = f"By{param_match.group(1).capitalize()}" if param_match else "ById"
                        namespaced_name = f"{namespaced_name}{suffix}"
                        bare_id = f"{bare_id}{suffix}"
                    elif "{" in existing_op.path and "}" in existing_op.path:
                        param_match = re.search(r"\{([^}]+)\}$", existing_op.path)
                        suffix = f"By{param_match.group(1).capitalize()}" if param_match else "ById"
                        new_existing_name = f"{existing_op.name}{suffix}"
                        new_existing_bare = f"{existing_op.bare_name}{suffix}"
                        existing_op.name = new_existing_name
                        existing_op.bare_name = new_existing_bare
                        self._operations[new_existing_name] = existing_op
                        self._bare_to_namespaced.setdefault(new_existing_bare, []).append(new_existing_name)
                        if existing_op.bare_name in self._bare_to_namespaced:
                            self._bare_to_namespaced[existing_op.bare_name] = [
                                n for n in self._bare_to_namespaced[existing_op.bare_name]
                                if n != existing_op.name
                            ]

                reg_op.name = namespaced_name
                reg_op.bare_name = bare_id
                self._operations[namespaced_name] = reg_op
                self._bare_to_namespaced.setdefault(bare_id, []).append(namespaced_name)

    def get_operation(self, name: str) -> Optional[RegistryOperation]:
        """Look up an operation by namespaced name, alias namespaced name, or bare operationId."""
        self.load()
        # Direct match:
        if name in self._operations:
            return self._operations[name]

        # Check alias (e.g. "phone-number-lookup.createSyncLookup" -> "lookup.createSyncLookup")
        if "." in name:
            prefix, rest = name.split(".", 1)
            canonical_prefix = SPEC_ALIASES.get(prefix)
            if canonical_prefix:
                canonical_name = f"{canonical_prefix}.{rest}"
                if canonical_name in self._operations:
                    return self._operations[canonical_name]

        # Bare name lookup (if unique)
        matches = self._bare_to_namespaced.get(name, [])
        if len(matches) == 1:
            return self._operations[matches[0]]

        return None

    def search(self, query: str, limit: int = 15) -> List[Dict[str, Any]]:
        """Search operations across all specs by keyword.

        Matches on namespaced name, bare name, path, summary, and description.
        Ranked by relevance score.
        """
        self.load()
        q = query.strip().lower()
        if not q:
            return []
        terms = [t for t in q.split() if t]

        scored: List[tuple[int, RegistryOperation]] = []
        for op in self._operations.values():
            haystack = f"{op.name} {op.bare_name} {op.path} {op.title} {op.description} {' '.join(op.tags)}".lower()
            score = 0
            all_matched = True
            for term in terms:
                if term not in haystack:
                    all_matched = False
                    break
                score += 1
                # Exact / prefix boosts
                if term == op.bare_name.lower() or term == op.name.lower():
                    score += 10
                elif op.bare_name.lower().startswith(term):
                    score += 5
                elif term in op.name.lower():
                    score += 3
                if term in op.path.lower():
                    score += 2

            if all_matched and score > 0:
                scored.append((score, op))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for _, op in scored[:limit]:
            desc = op.description.replace("\n", " ").strip()
            if len(desc) > 200:
                desc = desc[:197] + "..."
            results.append(
                {
                    "name": op.name,
                    "title": op.title,
                    "description": desc,
                    "method": op.method,
                    "path": op.path,
                    "is_write": op.is_write,
                    "is_destructive": op.is_destructive,
                    "spec": op.spec_name,
                }
            )
        return results

    @property
    def all_operations(self) -> Dict[str, RegistryOperation]:
        self.load()
        return dict(self._operations)


# Singleton instance
_REGISTRY = ApiRegistry()


def get_registry() -> ApiRegistry:
    """Return the global ApiRegistry singleton."""
    _REGISTRY.load()
    return _REGISTRY
