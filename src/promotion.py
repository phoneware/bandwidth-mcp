"""Per-user tool promotion tracking and storage.

Tracks how often a user invokes operations through call_api. When an operation
crosses the promotion threshold (default: 3 calls within 14 days), it is
automatically promoted into the user's exposed tool list so the AI client
sees it directly without needing search_api / call_api.

Two storage backends:
  - InMemoryUsageStore: used in local/stdio mode and unit tests.
  - FirestoreUsageStore: used in production on Cloud Run (collection: mcp_tool_usage).
"""

from __future__ import annotations

import base64
import os
import time
from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations

# Context variable for the current request's authenticated user
current_user_var: ContextVar[str] = ContextVar("current_user", default="")

DEFAULT_THRESHOLD = 3
DEFAULT_WINDOW_DAYS = 14


def get_current_user() -> str:
    """Return the currently authenticated user identifier."""
    user = current_user_var.get()
    if user:
        return user
    return os.environ.get("BW_USER") or os.environ.get("USER") or "local_user"


def promotion_enabled() -> bool:
    return os.environ.get("MCP_DISABLE_PROMOTION", "").lower() != "true"


def get_threshold() -> int:
    raw = os.environ.get("MCP_PROMOTE_THRESHOLD")
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return DEFAULT_THRESHOLD


def get_window_seconds() -> float:
    raw = os.environ.get("MCP_PROMOTE_WINDOW_DAYS")
    days = DEFAULT_WINDOW_DAYS
    if raw:
        try:
            val = float(raw)
            if val > 0:
                days = val
        except ValueError:
            pass
    return days * 86400.0


@dataclass
class UsageRecord:
    count: int
    last_used: float  # epoch seconds
    timestamps: List[float] = field(default_factory=list)

class UsageStore(ABC):
    @abstractmethod
    async def record_call(self, user_key: str, tool_name: str) -> UsageRecord:
        """Record an invocation and return updated UsageRecord."""
        ...

    @abstractmethod
    async def get_user_usage(self, user_key: str) -> Dict[str, UsageRecord]:
        """Return all usage records for a user keyed by tool name."""
        ...


class InMemoryUsageStore(UsageStore):
    """In-memory usage store for testing and single-session runs."""

    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, UsageRecord]] = {}

    async def record_call(self, user_key: str, tool_name: str) -> UsageRecord:
        user_records = self._data.setdefault(user_key, {})
        existing = user_records.get(tool_name)
        now = time.time()
        cutoff = now - get_window_seconds()
        existing_ts = (
            existing.timestamps
            if (existing and existing.timestamps)
            else ([existing.last_used] * existing.count if (existing and existing.last_used >= cutoff) else [])
        )
        valid_ts = [t for t in existing_ts if t >= cutoff]
        valid_ts.append(now)
        record = UsageRecord(count=len(valid_ts), last_used=now, timestamps=valid_ts)
        user_records[tool_name] = record
        return record

    async def get_user_usage(self, user_key: str) -> Dict[str, UsageRecord]:
        now = time.time()
        cutoff = now - get_window_seconds()
        raw = self._data.get(user_key, {})
        result = {}
        for tool_name, rec in raw.items():
            ts = (
                rec.timestamps
                if rec.timestamps
                else ([rec.last_used] * rec.count if rec.last_used >= cutoff else [])
            )
            valid_ts = [t for t in ts if t >= cutoff]
            if valid_ts:
                result[tool_name] = UsageRecord(
                    count=len(valid_ts),
                    last_used=max(valid_ts),
                    timestamps=valid_ts,
                )
        return result


class FirestoreUsageStore(UsageStore):
    """Firestore-backed usage store for Cloud Run production."""

    def __init__(self, collection_name: str = "mcp_tool_usage") -> None:
        from google.cloud import firestore  # type: ignore

        project = (
            os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GCP_PROJECT")
            or "phoneware-edge"
        )
        self._db = firestore.Client(project=project)
        self._collection = self._db.collection(collection_name)
        self._cache: Dict[str, tuple[float, Dict[str, UsageRecord]]] = {}
        self._cache_ttl = 30.0

    def _doc_id(self, user_key: str, tool_name: str) -> str:
        safe_user = base64.urlsafe_b64encode(user_key.encode()).rstrip(b"=").decode()
        return f"{safe_user}__{tool_name}"

    async def record_call(self, user_key: str, tool_name: str) -> UsageRecord:
        doc_id = self._doc_id(user_key, tool_name)
        doc_ref = self._collection.document(doc_id)
        now = time.time()
        cutoff = now - get_window_seconds()
        try:
            snapshot = doc_ref.get()
            valid_ts = []
            if snapshot.exists:
                data = snapshot.to_dict() or {}
                raw_ts = data.get("timestamps")
                if isinstance(raw_ts, list):
                    valid_ts = [float(t) for t in raw_ts if float(t) >= cutoff]
                else:
                    last_used = float(data.get("lastUsed", 0.0))
                    if last_used >= cutoff:
                        count = int(data.get("count", 1))
                        valid_ts = [last_used] * min(count, 100)
            valid_ts.append(now)
            count = len(valid_ts)
            doc_ref.set(
                {
                    "userKey": user_key,
                    "toolName": tool_name,
                    "count": count,
                    "lastUsed": now,
                    "timestamps": valid_ts,
                }
            )
            self._cache.pop(user_key, None)
            return UsageRecord(count=count, last_used=now, timestamps=valid_ts)
        except Exception as e:
            print(f"Warning: FirestoreUsageStore.record_call failed: {e}")
            return UsageRecord(count=1, last_used=now, timestamps=[now])

    async def get_user_usage(self, user_key: str) -> Dict[str, UsageRecord]:
        now = time.time()
        cutoff = now - get_window_seconds()
        cached = self._cache.get(user_key)
        if cached and (now - cached[0]) < self._cache_ttl:
            return dict(cached[1])

        try:
            query = self._collection.where("userKey", "==", user_key).stream()
            results: Dict[str, UsageRecord] = {}
            for doc in query:
                data = doc.to_dict() or {}
                t_name = data.get("toolName")
                if not t_name:
                    continue
                raw_ts = data.get("timestamps")
                if isinstance(raw_ts, list):
                    valid_ts = [float(t) for t in raw_ts if float(t) >= cutoff]
                else:
                    last_used = float(data.get("lastUsed", 0.0))
                    valid_ts = [last_used] if last_used >= cutoff else []
                if valid_ts:
                    results[t_name] = UsageRecord(
                        count=len(valid_ts),
                        last_used=max(valid_ts),
                        timestamps=valid_ts,
                    )
            self._cache[user_key] = (now, results)
            return results
        except Exception as e:
            print(f"Warning: FirestoreUsageStore.get_user_usage failed: {e}")
            return {}


_STORE: Optional[UsageStore] = None


def get_usage_store() -> UsageStore:
    """Return the active UsageStore singleton."""
    global _STORE
    if _STORE is not None:
        return _STORE

    use_firestore = os.environ.get("MCP_PERSISTENCE") == "firestore" or (
        os.environ.get("MCP_PERSISTENCE") != "file"
        and (
            bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))
            or bool(os.environ.get("GCP_PROJECT"))
            or bool(os.environ.get("K_SERVICE"))
        )
    )
    if use_firestore:
        try:
            _STORE = FirestoreUsageStore()
        except Exception as e:
            print(
                f"Warning: could not initialize FirestoreUsageStore ({e}), falling back to in-memory"
            )
            _STORE = InMemoryUsageStore()
    else:
        _STORE = InMemoryUsageStore()
    return _STORE


def set_usage_store_for_tests(store: Optional[UsageStore]) -> None:
    """Inject a test store instance."""
    global _STORE
    _STORE = store


async def get_promoted_tool_names(user_key: Optional[str] = None) -> List[str]:
    """Return the names of all tools currently promoted for the specified user."""
    if not promotion_enabled():
        return []
    uk = user_key or get_current_user()
    if not uk:
        return []

    store = get_usage_store()
    usage = await store.get_user_usage(uk)
    threshold = get_threshold()
    cutoff = time.time() - get_window_seconds()

    promoted: List[str] = []
    for tool_name, record in usage.items():
        if record.count >= threshold and record.last_used >= cutoff:
            promoted.append(tool_name)
    return promoted


async def record_call_api_invocation(
    user_key: Optional[str],
    tool_name: str,
    mcp_instance: Optional[FastMCP] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Record a call_api invocation and promote the tool if threshold reached."""
    if not promotion_enabled():
        return {"promoted": False, "count": 0}

    uk = user_key or get_current_user()
    if not uk:
        return {"promoted": False, "count": 0}

    store = get_usage_store()
    record = await store.record_call(uk, tool_name)
    threshold = get_threshold()

    was_promoted = record.count >= threshold
    if was_promoted and mcp_instance and config:
        promote_tool_on_server(mcp_instance, tool_name, config)
    return {"promoted": was_promoted, "count": record.count}


_PROMOTED_TOOLS: Set[str] = set()


def promote_tool_on_server(
    mcp_instance: FastMCP,
    tool_name: str,
    config: Dict[str, Any],
) -> bool:
    """Dynamically register a promoted operation as an exposed tool on FastMCP."""
    from registry import get_registry

    reg = get_registry()
    op = reg.get_operation(tool_name)
    if not op:
        return False

    _PROMOTED_TOOLS.add(op.name)

    # Check if already registered in local provider
    local_prov = getattr(mcp_instance, "local_provider", None)
    if local_prov and hasattr(local_prov, "_tools") and op.name in local_prov._tools:
        return True

    async def promoted_handler(args: Dict[str, Any] = {}, ctx: Context = None) -> Dict[str, Any]:
        from tools.meta import _dispatch_operation

        return await _dispatch_operation(op, args, config, ctx=ctx)

    try:
        mcp_instance.tool(
            name=op.name,
            title=op.title,
            description=f"[Promoted Tool] {op.description}",
            annotations=op.annotations,
        )(promoted_handler)
        return True
    except Exception as e:
        print(f"Warning: could not dynamically register promoted tool {op.name}: {e}")
        return False


def setup_promotions(mcp_instance: FastMCP, config: Dict[str, Any]) -> None:
    """Wire per-user tool promotion filters onto FastMCP."""
    orig_list_tools = mcp_instance._list_tools
    orig_get_tool = mcp_instance._get_tool

    async def _user_scoped_list_tools():
        all_tools = await orig_list_tools()
        if not promotion_enabled():
            return [
                t
                for t in all_tools
                if t.name not in _PROMOTED_TOOLS
                and not (t.description and t.description.startswith("[Promoted Tool]"))
            ]

        current_user = get_current_user()
        promoted_for_user = set(await get_promoted_tool_names(current_user))
        filtered = []
        seen = set()

        for t in all_tools:
            if t.name in _PROMOTED_TOOLS or (
                t.description and t.description.startswith("[Promoted Tool]")
            ):
                if t.name not in promoted_for_user:
                    continue
            filtered.append(t)
            seen.add(t.name)

        # Restore any persisted promotions that crossed threshold for this user
        for p_name in promoted_for_user:
            if p_name not in seen:
                promote_tool_on_server(mcp_instance, p_name, config)
                t = await orig_get_tool(p_name)
                if t:
                    filtered.append(t)
                    seen.add(p_name)

        return filtered

    async def _user_scoped_get_tool(name: str, version=None):
        current_user = get_current_user()
        promoted_for_user = (
            set(await get_promoted_tool_names(current_user))
            if promotion_enabled()
            else set()
        )

        tool = await orig_get_tool(name, version)
        if tool is None and promotion_enabled() and name in promoted_for_user:
            promote_tool_on_server(mcp_instance, name, config)
            tool = await orig_get_tool(name, version)

        if tool is not None and (
            tool.name in _PROMOTED_TOOLS
            or (tool.description and tool.description.startswith("[Promoted Tool]"))
        ):
            if tool.name not in promoted_for_user:
                return None  # Scoped to user who earned promotion

        return tool

    mcp_instance._list_tools = _user_scoped_list_tools
    mcp_instance._get_tool = _user_scoped_get_tool
