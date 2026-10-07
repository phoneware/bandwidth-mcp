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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP
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
        new_count = (existing.count + 1) if existing else 1
        record = UsageRecord(count=new_count, last_used=time.time())
        user_records[tool_name] = record
        return record

    async def get_user_usage(self, user_key: str) -> Dict[str, UsageRecord]:
        return dict(self._data.get(user_key, {}))


class FirestoreUsageStore(UsageStore):
    """Firestore-backed usage store for Cloud Run production."""

    def __init__(self, collection_name: str = "mcp_tool_usage") -> None:
        from google.cloud import firestore  # type: ignore

        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get(
            "GCP_PROJECT"
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
        try:
            snapshot = doc_ref.get()
            count = 1
            if snapshot.exists:
                data = snapshot.to_dict() or {}
                count = int(data.get("count", 0)) + 1
            doc_ref.set(
                {
                    "userKey": user_key,
                    "toolName": tool_name,
                    "count": count,
                    "lastUsed": now,
                }
            )
            # Invalidate cache for user
            self._cache.pop(user_key, None)
            return UsageRecord(count=count, last_used=now)
        except Exception as e:
            # Usage tracking is best-effort and must not fail tool execution
            print(f"Warning: FirestoreUsageStore.record_call failed: {e}")
            return UsageRecord(count=1, last_used=now)

    async def get_user_usage(self, user_key: str) -> Dict[str, UsageRecord]:
        now = time.time()
        cached = self._cache.get(user_key)
        if cached and (now - cached[0]) < self._cache_ttl:
            return dict(cached[1])

        try:
            query = self._collection.where("userKey", "==", user_key).stream()
            results: Dict[str, UsageRecord] = {}
            for doc in query:
                data = doc.to_dict() or {}
                t_name = data.get("toolName")
                if t_name:
                    results[t_name] = UsageRecord(
                        count=int(data.get("count", 0)),
                        last_used=float(data.get("lastUsed", 0.0)),
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
        and bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))
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

    was_promoted = record.count == threshold
    if was_promoted and mcp_instance and config:
        promote_tool_on_server(mcp_instance, tool_name, config)

    return {"promoted": was_promoted, "count": record.count}


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

    # Check if already registered in local provider
    local_prov = getattr(mcp_instance, "local_provider", None)
    if local_prov and hasattr(local_prov, "_tools") and op.name in local_prov._tools:
        return True

    async def promoted_handler(args: Dict[str, Any] = {}) -> Dict[str, Any]:
        from tools.meta import _dispatch_operation

        return await _dispatch_operation(op, args, config, ctx=None)

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
