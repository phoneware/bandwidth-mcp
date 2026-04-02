# MCP Server v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the MCP server from a stdio-only API wrapper into a hosted, self-documenting agent platform with callbacks and programmable voice.

**Architecture:** Add dynamic `instructions` field to FastMCP constructor so LLM clients auto-discover tool usage. Switch transport via config flag (stdio/sse/streamable-http). Mount Starlette callback routes on the same process for inbound webhooks. Add BXML generation tools for voice. All backed by an in-memory event store with per-session read cursors.

**Tech Stack:** Python 3.10+, FastMCP ~2.13.0, httpx, Starlette (bundled with FastMCP), pytest, pytest-asyncio, pytest-httpx

**Spec:** `docs/specs/2026-04-02-mcp-server-v2-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/instructions.py` | Create | Dynamic instructions builder — adapts to loaded tools and config |
| `src/profiles.py` | Create | Tool profile definitions (messaging, voice, onboarding, lookup, full) |
| `src/config.py` | Modify | Add transport, profile, base URL, fallback number, auth token config |
| `src/server_utils.py` | Modify | Add spec caching (fetch → cache → fallback) |
| `src/app.py` | Modify | Wire instructions, transport config, mount callback routes |
| `src/event_store.py` | Create | In-memory ring buffer with per-session read cursors, call state |
| `src/callbacks.py` | Create | Starlette callback routes for messaging and voice webhooks |
| `src/tools/callbacks.py` | Create | getInboundMessages, getCallbackEvents, configureCallbacks tools |
| `src/tools/voice.py` | Create | generateBXML, respondToCallback, setVoiceHandler tools |
| `test/test_instructions.py` | Create | Instructions builder tests |
| `test/test_profiles.py` | Create | Profile resolution tests |
| `test/test_spec_cache.py` | Create | Spec caching tests |
| `test/test_event_store.py` | Create | Event store tests |
| `test/test_callbacks.py` | Create | Callback route tests |
| `test/test_bxml.py` | Create | BXML generation tests |
| `test/test_voice_callbacks.py` | Create | Voice callback flow tests (redirect chain, respond, fallback) |

---

### Task 1: Dynamic Instructions Builder

**Files:**
- Create: `src/instructions.py`
- Create: `test/test_instructions.py`

- [ ] **Step 1: Write failing test for instructions builder**

```python
# test/test_instructions.py
import pytest
from src.instructions import build_instructions


def test_build_instructions_includes_header():
    """Instructions always start with the server identity."""
    result = build_instructions(config={}, loaded_tools=[])
    assert "Bandwidth MCP Server" in result


def test_build_instructions_includes_messaging_when_tools_loaded():
    """Messaging section appears when messaging tools are loaded."""
    result = build_instructions(config={}, loaded_tools=["createMessage", "listMessages"])
    assert "createMessage" in result
    assert "SMS" in result or "send" in result.lower()


def test_build_instructions_excludes_messaging_when_no_tools():
    """Messaging section absent when no messaging tools loaded."""
    result = build_instructions(config={}, loaded_tools=["createLookup"])
    assert "createMessage" not in result


def test_build_instructions_includes_no_credentials_warning():
    """Warning appears when no username in config."""
    result = build_instructions(config={}, loaded_tools=[])
    assert "setCredentials" in result or "Express Registration" in result


def test_build_instructions_no_warning_when_credentials_present():
    """No credential warning when username is set."""
    result = build_instructions(
        config={"BW_USERNAME": "user", "BW_PASSWORD": "pass"},
        loaded_tools=["createMessage"],
    )
    assert "no credentials" not in result.lower()


def test_build_instructions_includes_lookup_section():
    """Lookup section appears when lookup tools loaded."""
    result = build_instructions(config={}, loaded_tools=["createLookup", "getLookupStatus"])
    assert "createLookup" in result
    assert "getLookupStatus" in result


def test_build_instructions_includes_voice_section():
    """Voice section appears when voice tools loaded."""
    result = build_instructions(config={}, loaded_tools=["createCall", "generateBXML"])
    assert "createCall" in result
    assert "BXML" in result


def test_build_instructions_includes_mfa_section():
    """MFA section appears when MFA tools loaded."""
    result = build_instructions(
        config={}, loaded_tools=["generateMessagingCode", "verifyCode"]
    )
    assert "generateMessagingCode" in result
    assert "verifyCode" in result


def test_build_instructions_includes_callback_section():
    """Callback section appears when callback tools loaded."""
    result = build_instructions(
        config={}, loaded_tools=["getCallbackEvents", "getInboundMessages"]
    )
    assert "getCallbackEvents" in result


def test_build_instructions_includes_error_patterns():
    """Error patterns section is always included."""
    result = build_instructions(config={}, loaded_tools=[])
    assert "401" in result
    assert "422" in result
    assert "E.164" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test/test_instructions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.instructions'`

- [ ] **Step 3: Implement instructions builder**

```python
# src/instructions.py
"""Dynamic MCP instructions builder.

Generates the instructions string sent to LLM clients during MCP initialization.
Adapts content based on which tools are actually loaded and what config is present.
"""

from typing import Any

HEADER = """# Bandwidth MCP Server

You have access to Bandwidth's communication APIs as MCP tools. Use them to send messages, make calls, look up numbers, and more.

## Quick Start
- Read resource://config to check what credentials and application IDs are loaded.
- Phone numbers must be in E.164 format (e.g. +19195551234).
- Application IDs are UUIDs."""

NO_CREDENTIALS_SECTION = """
## No Credentials Detected
No API credentials are configured. You can either:
1. Use Express Registration to create a new account:
   createRegistration → sendVerificationCode → verifyRegistrationCode → setCredentials
2. Ask the user to provide BW_USERNAME, BW_PASSWORD, and BW_ACCOUNT_ID."""

MESSAGING_SECTION = """
## Sending Messages (SMS/MMS)
Requires: BW_ACCOUNT_ID, BW_MESSAGING_APPLICATION_ID, BW_NUMBER
- **createMessage**: Send an SMS or MMS. Provide `to`, `from` (your BW_NUMBER), `applicationId` (BW_MESSAGING_APPLICATION_ID), and `text`.
- **listMessages**: Search message history or check delivery status.
- **createMultiChannelMessage**: Send via RBM, SMS, or MMS with channel fallback."""

LOOKUP_SECTION = """
## Phone Number Lookup
Requires: BW_ACCOUNT_ID
- **createLookup** then **getLookupStatus**: Async operation. Create the lookup, take the requestId from the response, poll getLookupStatus until status is complete. Don't treat "pending" as failure."""

MFA_SECTION = """
## Multi-Factor Authentication
Requires: BW_ACCOUNT_ID, BW_NUMBER, application ID for chosen channel
- **generateMessagingCode**: Send MFA code via SMS.
- **generateVoiceCode**: Send MFA code via voice call.
- **verifyCode**: Verify a previously sent code. Provide `to`, `scope`, and the entered `code`."""

VOICE_SECTION = """
## Voice Calls & BXML
Requires: BW_ACCOUNT_ID, voice application with callback URL configured
- **createCall**: Initiate an outbound call.
- **generateBXML**: Produce valid BXML from verb descriptions (SpeakSentence, Gather, Transfer, Record, etc.).
- **respondToCallback**: Queue a BXML response for an active call after reading gather results.
- **getCallbackEvents**: Read inbound call events including transcribed speech.
- Always wrap SpeakSentence in Gather for barge-in (caller can interrupt).
- For structured input, use input_type "speech dtmf" so callers can speak or press keys."""

CALLBACK_SECTION = """
## Inbound Events & Callbacks
- **getInboundMessages**: Get recent inbound SMS/MMS received by your number.
- **getCallbackEvents**: Get all callback events (voice + messaging), filterable by type, call ID, or phone number.
- **configureCallbacks**: Point a Bandwidth application's webhook URLs at this server. Self-configuring — one call and webhooks are wired."""

REPORTING_SECTION = """
## Reporting & Analytics
Requires: BW_ACCOUNT_ID
- **createReport** → **getReportStatus** → **getReportFile**: Async report generation. Create, poll status, then download."""

REGISTRATION_SECTION = """
## Express Registration (No Auth Required)
- **createRegistration**: Start a new Bandwidth account.
- **sendVerificationCode**: Send SMS verification to the registered number.
- **verifyRegistrationCode**: Confirm the code.
Then call **setCredentials** with the new username, password, and account_id to unlock authenticated tools."""

ERROR_SECTION = """
## Error Patterns
- **401 Unauthorized**: Wrong credentials. Check BW_USERNAME/BW_PASSWORD.
- **422 Validation Error**: Missing or malformed fields. Phone numbers must be E.164 (+19195551234). Application IDs are UUIDs.
- **"Tool not found"**: Check BW_MCP_TOOLS / BW_MCP_EXCLUDE_TOOLS filters.
- **"Pending" responses**: Lookup and reporting are async — poll the status tool, don't treat pending as failure.
- **No authenticated tools**: Credentials weren't set. Use Express Registration flow or set env vars."""


# Mapping: if ANY of these tools are loaded, include the section
_SECTION_TRIGGERS: list[tuple[list[str], str]] = [
    (["createRegistration", "sendVerificationCode", "verifyRegistrationCode"], REGISTRATION_SECTION),
    (["createMessage", "listMessages", "createMultiChannelMessage"], MESSAGING_SECTION),
    (["createLookup", "getLookupStatus", "createSyncLookup", "createAsyncBulkLookup"], LOOKUP_SECTION),
    (["generateMessagingCode", "generateVoiceCode", "verifyCode"], MFA_SECTION),
    (["createCall", "generateBXML", "respondToCallback"], VOICE_SECTION),
    (["getInboundMessages", "getCallbackEvents", "configureCallbacks"], CALLBACK_SECTION),
    (["createReport", "getReportStatus", "getReportFile"], REPORTING_SECTION),
]


def build_instructions(config: dict[str, Any], loaded_tools: list[str]) -> str:
    """Build the MCP instructions string based on loaded tools and config.

    Args:
        config: Server configuration dict (credentials, app IDs, etc.)
        loaded_tools: List of tool names currently registered on the server.

    Returns:
        Instructions string for the MCP initialization handshake.
    """
    sections = [HEADER]

    if not config.get("BW_USERNAME"):
        sections.append(NO_CREDENTIALS_SECTION)

    tool_set = set(loaded_tools)
    for trigger_tools, section in _SECTION_TRIGGERS:
        if tool_set & set(trigger_tools):
            sections.append(section)

    sections.append(ERROR_SECTION)

    return "\n".join(sections)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test/test_instructions.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/instructions.py test/test_instructions.py
git commit -m "feat: add dynamic instructions builder for MCP initialization"
```

---

### Task 2: Tool Profiles

**Files:**
- Create: `src/profiles.py`
- Create: `test/test_profiles.py`
- Modify: `src/config.py`

- [ ] **Step 1: Write failing tests for profiles**

```python
# test/test_profiles.py
import pytest
from src.profiles import resolve_profile


def test_resolve_single_profile():
    """Single profile returns its tool list."""
    tools = resolve_profile("messaging")
    assert "createMessage" in tools
    assert "listMessages" in tools


def test_resolve_combined_profiles():
    """Comma-separated profiles merge their tool lists."""
    tools = resolve_profile("messaging,lookup")
    assert "createMessage" in tools
    assert "createLookup" in tools


def test_resolve_full_profile():
    """Full profile returns None (meaning all tools)."""
    tools = resolve_profile("full")
    assert tools is None


def test_resolve_unknown_profile_raises():
    """Unknown profile name raises ValueError."""
    with pytest.raises(ValueError, match="Unknown profile"):
        resolve_profile("nonexistent")


def test_resolve_none_returns_none():
    """No profile returns None."""
    tools = resolve_profile(None)
    assert tools is None


def test_resolve_empty_string_returns_none():
    """Empty string returns None."""
    tools = resolve_profile("")
    assert tools is None


def test_voice_profile():
    """Voice profile includes BXML and callback tools."""
    tools = resolve_profile("voice")
    assert "createCall" in tools
    assert "generateBXML" in tools
    assert "getCallbackEvents" in tools


def test_onboarding_profile():
    """Onboarding profile includes registration and credentials."""
    tools = resolve_profile("onboarding")
    assert "createRegistration" in tools
    assert "setCredentials" in tools
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test/test_profiles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.profiles'`

- [ ] **Step 3: Implement profiles module**

```python
# src/profiles.py
"""Tool profile presets for reducing context window pressure.

Profiles are named sets of tools. Use BW_MCP_PROFILE env var or --profile CLI flag.
"""

from typing import Optional

PROFILES: dict[str, list[str]] = {
    "messaging": [
        "createMessage",
        "listMessages",
        "createMultiChannelMessage",
        "getInboundMessages",
        "getMessageStatus",
        "configureCallbacks",
        "listMedia",
        "getMedia",
        "uploadMedia",
        "deleteMedia",
    ],
    "voice": [
        "createCall",
        "generateBXML",
        "respondToCallback",
        "getCallbackEvents",
        "configureCallbacks",
        "setVoiceHandler",
    ],
    "onboarding": [
        "createRegistration",
        "sendVerificationCode",
        "verifyRegistrationCode",
        "setCredentials",
    ],
    "lookup": [
        "createLookup",
        "getLookupStatus",
        "createSyncLookup",
        "createAsyncBulkLookup",
        "getAsyncBulkLookup",
    ],
}


def resolve_profile(profile_str: Optional[str]) -> Optional[list[str]]:
    """Resolve a profile string into a list of tool names.

    Args:
        profile_str: Comma-separated profile names, "full", None, or empty string.

    Returns:
        List of tool names to enable, or None for all tools.

    Raises:
        ValueError: If a profile name is not recognized.
    """
    if not profile_str:
        return None

    names = [p.strip() for p in profile_str.split(",") if p.strip()]
    if not names:
        return None

    if "full" in names:
        return None

    tools: list[str] = []
    for name in names:
        if name not in PROFILES:
            raise ValueError(f"Unknown profile: '{name}'. Available: {', '.join(sorted(PROFILES.keys()))}, full")
        tools.extend(PROFILES[name])

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for t in tools:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test/test_profiles.py -v`
Expected: All PASS

- [ ] **Step 5: Wire profiles into config.py**

Add to `src/config.py`:

```python
# At top of file, add import:
from profiles import resolve_profile

# Add to _parse_cli_args, after the --exclude-tools argument:
    parser.add_argument(
        "--profile",
        help="Tool profile preset (messaging, voice, onboarding, lookup, full). Comma-separate to combine.",
        type=str,
    )

# Add new function:
def get_profile_tools() -> Optional[List[str]]:
    """Get tool list from profile, if specified."""
    args = _parse_cli_args()
    profile_str = args.profile or os.getenv("BW_MCP_PROFILE")
    return resolve_profile(profile_str)
```

Update `get_enabled_tools` to incorporate profiles:

```python
def get_enabled_tools() -> Optional[List[str]]:
    """Get the list of enabled tools from CLI args, env var, or profile.
    
    Priority: --tools flag > BW_MCP_TOOLS env > --profile flag > BW_MCP_PROFILE env
    """
    args = _parse_cli_args()
    explicit = _parse_flags(args.tools, "BW_MCP_TOOLS")
    if explicit:
        return explicit
    return get_profile_tools()
```

- [ ] **Step 6: Run all tests**

Run: `pytest -v`
Expected: All PASS (existing tests unaffected, new tests pass)

- [ ] **Step 7: Commit**

```bash
git add src/profiles.py test/test_profiles.py src/config.py
git commit -m "feat: add tool profiles for context window management"
```

---

### Task 3: Spec Caching

**Files:**
- Modify: `src/server_utils.py`
- Create: `test/test_spec_cache.py`

- [ ] **Step 1: Write failing tests for spec caching**

```python
# test/test_spec_cache.py
import pytest
import yaml
from pathlib import Path
from unittest.mock import patch, AsyncMock
from src.server_utils import fetch_openapi_spec, _save_spec_cache, _load_spec_cache, CACHE_DIR


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    """Redirect spec cache to a temp directory."""
    monkeypatch.setattr("src.server_utils.CACHE_DIR", tmp_path)
    return tmp_path


SAMPLE_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Test", "version": "1.0.0"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {},
}


def test_save_and_load_cache(tmp_cache):
    """Cached spec round-trips correctly."""
    url = "https://dev.bandwidth.com/spec/test.yml"
    _save_spec_cache(url, SAMPLE_SPEC)
    loaded = _load_spec_cache(url)
    assert loaded == SAMPLE_SPEC


def test_load_cache_returns_none_when_missing(tmp_cache):
    """Missing cache returns None."""
    loaded = _load_spec_cache("https://dev.bandwidth.com/spec/missing.yml")
    assert loaded is None


@pytest.mark.asyncio
async def test_fetch_caches_on_success(tmp_cache, httpx_mock):
    """Successful fetch saves to cache."""
    url = "https://dev.bandwidth.com/spec/cached-test.yml"
    httpx_mock.add_response(url=url, text=yaml.dump(SAMPLE_SPEC))

    result = await fetch_openapi_spec(url)
    assert result["info"]["title"] == "Test"

    cached = _load_spec_cache(url)
    assert cached is not None
    assert cached["info"]["title"] == "Test"


@pytest.mark.asyncio
async def test_fetch_falls_back_to_cache(tmp_cache, httpx_mock):
    """Failed fetch uses cached spec."""
    url = "https://dev.bandwidth.com/spec/fallback-test.yml"
    _save_spec_cache(url, SAMPLE_SPEC)

    httpx_mock.add_exception(httpx.ConnectError("Network down"), url=url)

    result = await fetch_openapi_spec(url)
    assert result["info"]["title"] == "Test"


@pytest.mark.asyncio
async def test_fetch_raises_when_no_cache_no_network(tmp_cache, httpx_mock):
    """No cache + no network = raise."""
    url = "https://dev.bandwidth.com/spec/nowhere.yml"
    httpx_mock.add_exception(httpx.ConnectError("Network down"), url=url)

    with pytest.raises(RuntimeError, match="Failed to fetch"):
        await fetch_openapi_spec(url)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test/test_spec_cache.py -v`
Expected: FAIL — `ImportError: cannot import name '_save_spec_cache'`

- [ ] **Step 3: Add spec caching to server_utils.py**

Add to `src/server_utils.py` after the imports:

```python
import hashlib
import warnings
from pathlib import Path

CACHE_DIR = Path.home() / ".bw-mcp" / "spec-cache"


def _cache_key(url: str) -> str:
    """Generate a filesystem-safe cache key from a URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:16] + ".yml"


def _save_spec_cache(url: str, spec: dict[str, Any]) -> None:
    """Save a spec to the local cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / _cache_key(url)
    cache_path.write_text(yaml.dump(spec), encoding="utf-8")


def _load_spec_cache(url: str) -> dict[str, Any] | None:
    """Load a spec from the local cache, or None if not cached."""
    cache_path = CACHE_DIR / _cache_key(url)
    if not cache_path.exists():
        return None
    try:
        return yaml.safe_load(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None
```

Then update `fetch_openapi_spec` to use caching:

```python
async def fetch_openapi_spec(url: str) -> dict[str, Any]:
    """Fetch and parse OpenAPI spec from URL, with local cache fallback."""
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
    except Exception as e:
        cached = _load_spec_cache(url)
        if cached:
            warnings.warn(f"Using cached spec for {url}: {e}")
            return cached
        raise RuntimeError(f"Failed to fetch OpenAPI spec from {url}: {e}") from e
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test/test_spec_cache.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/server_utils.py test/test_spec_cache.py
git commit -m "feat: add local spec caching with fallback on network failure"
```

---

### Task 4: Wire Instructions into FastMCP

**Files:**
- Modify: `src/app.py`

- [ ] **Step 1: Update app.py to build and pass instructions**

Replace the `mcp` instantiation and `setup` function in `src/app.py`:

```python
import asyncio
import os
import warnings

os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

from fastmcp import FastMCP
from servers import create_bandwidth_mcp, api_server_info, _create_server
from config import load_config, get_enabled_tools, get_excluded_tools
from server_utils import create_route_map_fn
from tools.credentials import register_credentials_tools
from instructions import build_instructions

mcp = FastMCP(name="Bandwidth MCP")
_config = {}


async def _reload_authenticated_servers():
    """Load authenticated API servers after credentials are set mid-session."""
    if _config.get("_authenticated_servers_loaded"):
        return
    _config["_authenticated_servers_loaded"] = True

    enabled_tools = get_enabled_tools()
    excluded_tools = get_excluded_tools()
    route_map_fn = create_route_map_fn(enabled_tools, excluded_tools)

    for api_name, api_info in api_server_info.items():
        requires_auth = api_info.get("requires_auth", True)
        if not requires_auth:
            continue
        try:
            server = await _create_server(
                url=api_info["url"],
                route_map_fn=route_map_fn,
                config=_config,
                requires_auth=True,
            )
            await mcp.import_server(server)
        except Exception as e:
            warnings.warn(f"Failed to load {api_name} after credential update: {e}")

    # Rebuild instructions with newly loaded tools
    all_tools = await mcp.get_tools()
    mcp.instructions = build_instructions(_config, list(all_tools.keys()))


async def setup(mcp: FastMCP = mcp):
    """Setup the Bandwidth MCP server with tools and resources."""
    global _config
    enabled_tools = get_enabled_tools()
    excluded_tools = get_excluded_tools()
    _config = load_config()

    print("Setting up Bandwidth MCP server...")
    await create_bandwidth_mcp(mcp, enabled_tools, excluded_tools, _config)

    register_credentials_tools(mcp, _config, reload_callback=_reload_authenticated_servers)

    # Build and set instructions based on loaded tools
    all_tools = await mcp.get_tools()
    mcp.instructions = build_instructions(_config, list(all_tools.keys()))


def main():
    """Main function to run the Bandwidth MCP server."""
    asyncio.run(setup())
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run full test suite**

Run: `pytest -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/app.py
git commit -m "feat: wire dynamic instructions into FastMCP initialization"
```

---

### Task 5: Event Store

**Files:**
- Create: `src/event_store.py`
- Create: `test/test_event_store.py`

- [ ] **Step 1: Write failing tests for event store**

```python
# test/test_event_store.py
import time
import pytest
from src.event_store import EventStore, CallState


class TestEventStore:
    def setup_method(self):
        self.store = EventStore(max_events=100, ttl_seconds=3600)

    def test_push_and_get_events(self):
        """Push events and retrieve them."""
        self.store.push("messaging.inbound", "+19195551234", {"text": "hello"})
        self.store.push("messaging.inbound", "+19195551234", {"text": "world"})
        events = self.store.get_events("messaging.inbound", key="+19195551234")
        assert len(events) == 2
        assert events[0]["text"] == "hello"
        assert events[1]["text"] == "world"

    def test_get_events_empty(self):
        """No events returns empty list."""
        events = self.store.get_events("messaging.inbound", key="+10000000000")
        assert events == []

    def test_get_events_filtered_by_since(self):
        """Events can be filtered by timestamp."""
        self.store.push("messaging.inbound", "+19195551234", {"text": "old"})
        cutoff = time.time()
        self.store.push("messaging.inbound", "+19195551234", {"text": "new"})
        events = self.store.get_events("messaging.inbound", key="+19195551234", since=cutoff)
        assert len(events) == 1
        assert events[0]["text"] == "new"

    def test_max_events_ring_buffer(self):
        """Oldest events evicted when max reached."""
        store = EventStore(max_events=3, ttl_seconds=3600)
        for i in range(5):
            store.push("test", "key", {"n": i})
        events = store.get_events("test", key="key")
        assert len(events) == 3
        assert events[0]["n"] == 2  # oldest surviving

    def test_get_all_events_by_type(self):
        """Get all events of a type across all keys."""
        self.store.push("messaging.inbound", "+11111111111", {"text": "a"})
        self.store.push("messaging.inbound", "+12222222222", {"text": "b"})
        events = self.store.get_events("messaging.inbound")
        assert len(events) == 2

    def test_session_cursor_isolation(self):
        """Per-session cursors track read position independently."""
        self.store.push("messaging.inbound", "+19195551234", {"text": "msg1"})
        self.store.push("messaging.inbound", "+19195551234", {"text": "msg2"})

        # Session A reads both
        events_a = self.store.get_unread("messaging.inbound", session_id="session-a")
        assert len(events_a) == 2

        # Session B also gets both (independent cursor)
        events_b = self.store.get_unread("messaging.inbound", session_id="session-b")
        assert len(events_b) == 2

        # Push a new event
        self.store.push("messaging.inbound", "+19195551234", {"text": "msg3"})

        # Both sessions only get the new one
        events_a2 = self.store.get_unread("messaging.inbound", session_id="session-a")
        assert len(events_a2) == 1
        assert events_a2[0]["text"] == "msg3"

        events_b2 = self.store.get_unread("messaging.inbound", session_id="session-b")
        assert len(events_b2) == 1


class TestCallState:
    def setup_method(self):
        self.store = EventStore(max_events=100, ttl_seconds=3600)

    def test_create_and_get_call(self):
        """Create a call state and retrieve it."""
        self.store.create_call("call-123", from_number="+11111111111", to_number="+12222222222", application_id="app-1")
        call = self.store.get_call("call-123")
        assert call is not None
        assert call.call_id == "call-123"
        assert call.from_number == "+11111111111"
        assert call.turns == []

    def test_get_missing_call(self):
        """Missing call returns None."""
        assert self.store.get_call("nonexistent") is None

    def test_add_turn(self):
        """Turns are appended to call state."""
        self.store.create_call("call-123", "+11111111111", "+12222222222", "app-1")
        call = self.store.get_call("call-123")
        call.add_turn("caller", "Hello?")
        call.add_turn("agent", "Hi, how can I help?")
        assert len(call.turns) == 2
        assert call.turns[0]["role"] == "caller"
        assert call.turns[1]["text"] == "Hi, how can I help?"

    def test_pending_bxml(self):
        """Pending BXML can be set and consumed."""
        self.store.create_call("call-123", "+11111111111", "+12222222222", "app-1")
        call = self.store.get_call("call-123")
        call.pending_bxml = "<Response><Hangup/></Response>"
        assert call.pending_bxml == "<Response><Hangup/></Response>"
        bxml = call.consume_pending_bxml()
        assert bxml == "<Response><Hangup/></Response>"
        assert call.pending_bxml is None

    def test_consume_pending_bxml_returns_none_when_empty(self):
        """Consuming when no BXML pending returns None."""
        self.store.create_call("call-123", "+11111111111", "+12222222222", "app-1")
        call = self.store.get_call("call-123")
        assert call.consume_pending_bxml() is None

    def test_first_write_wins_for_bxml(self):
        """Second write to pending_bxml returns False (already queued)."""
        self.store.create_call("call-123", "+11111111111", "+12222222222", "app-1")
        call = self.store.get_call("call-123")
        assert call.try_set_bxml("<Response><Hangup/></Response>") is True
        assert call.try_set_bxml("<Response><SpeakSentence>Too late</SpeakSentence></Response>") is False
        assert "Hangup" in call.pending_bxml

    def test_remove_call(self):
        """Calls can be removed (disconnect cleanup)."""
        self.store.create_call("call-123", "+11111111111", "+12222222222", "app-1")
        self.store.remove_call("call-123")
        assert self.store.get_call("call-123") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test/test_event_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.event_store'`

- [ ] **Step 3: Implement event store**

```python
# src/event_store.py
"""In-memory event store for callback events and call state.

Ring buffer per event type+key, with per-session read cursors for multi-session
safety and first-write-wins on voice BXML responses.
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class CallState:
    """Per-call conversation state."""

    call_id: str
    from_number: str
    to_number: str
    application_id: str
    started_at: float = field(default_factory=time.time)
    turns: list[dict] = field(default_factory=list)
    pending_bxml: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def add_turn(self, role: str, text: str) -> None:
        """Append a conversation turn."""
        self.turns.append({"role": role, "text": text, "timestamp": time.time()})

    def try_set_bxml(self, bxml: str) -> bool:
        """Set pending BXML if not already set. Returns True if set, False if already occupied."""
        if self.pending_bxml is not None:
            return False
        self.pending_bxml = bxml
        return True

    def consume_pending_bxml(self) -> Optional[str]:
        """Take the pending BXML and clear it. Returns None if nothing pending."""
        bxml = self.pending_bxml
        self.pending_bxml = None
        return bxml


class EventStore:
    """In-memory ring buffer for callback events with per-session cursors."""

    def __init__(self, max_events: int = 1000, ttl_seconds: int = 3600):
        self._max_events = max_events
        self._ttl = ttl_seconds
        self._events: dict[str, deque[dict]] = defaultdict(
            lambda: deque(maxlen=max_events)
        )
        self._global_counter: int = 0
        self._session_cursors: dict[str, int] = {}
        self._calls: dict[str, CallState] = {}

    def push(self, event_type: str, key: str, event: dict) -> None:
        """Store an event."""
        self._global_counter += 1
        event = {**event, "_received_at": time.time(), "_seq": self._global_counter}
        self._events[f"{event_type}:{key}"].append(event)
        self._events[event_type].append(event)

    def get_events(
        self,
        event_type: str,
        key: Optional[str] = None,
        since: Optional[float] = None,
    ) -> list[dict]:
        """Retrieve events, optionally filtered by key and timestamp."""
        bucket = f"{event_type}:{key}" if key else event_type
        events = list(self._events.get(bucket, []))

        now = time.time()
        events = [e for e in events if now - e["_received_at"] < self._ttl]

        if since is not None:
            events = [e for e in events if e["_received_at"] > since]

        return events

    def get_unread(self, event_type: str, session_id: str) -> list[dict]:
        """Get events not yet seen by this session."""
        cursor_key = f"{session_id}:{event_type}"
        last_seq = self._session_cursors.get(cursor_key, 0)

        events = list(self._events.get(event_type, []))
        unread = [e for e in events if e["_seq"] > last_seq]

        if unread:
            self._session_cursors[cursor_key] = unread[-1]["_seq"]

        return unread

    def create_call(
        self,
        call_id: str,
        from_number: str,
        to_number: str,
        application_id: str,
    ) -> CallState:
        """Create a new call state."""
        call = CallState(
            call_id=call_id,
            from_number=from_number,
            to_number=to_number,
            application_id=application_id,
        )
        self._calls[call_id] = call
        return call

    def get_call(self, call_id: str) -> Optional[CallState]:
        """Get call state by ID."""
        return self._calls.get(call_id)

    def remove_call(self, call_id: str) -> None:
        """Remove a call state (disconnect cleanup)."""
        self._calls.pop(call_id, None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test/test_event_store.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/event_store.py test/test_event_store.py
git commit -m "feat: add in-memory event store with per-session cursors and call state"
```

---

### Task 6: Callback HTTP Routes

**Files:**
- Create: `src/callbacks.py`
- Create: `test/test_callbacks.py`

- [ ] **Step 1: Write failing tests for callback routes**

```python
# test/test_callbacks.py
import pytest
from starlette.testclient import TestClient
from src.callbacks import create_callback_app
from src.event_store import EventStore


@pytest.fixture
def event_store():
    return EventStore(max_events=100, ttl_seconds=3600)


@pytest.fixture
def client(event_store):
    app = create_callback_app(event_store)
    return TestClient(app)


class TestMessagingCallbacks:
    def test_inbound_message(self, client, event_store):
        """Inbound messaging callback stores event and returns 200."""
        payload = [
            {
                "type": "message-received",
                "message": {
                    "from": "+19195551234",
                    "to": ["+19195554321"],
                    "text": "Hello from tests",
                    "id": "msg-abc123",
                },
            }
        ]
        response = client.post("/callbacks/messaging/inbound", json=payload)
        assert response.status_code == 200

        events = event_store.get_events("messaging.inbound")
        assert len(events) == 1
        assert events[0]["message"]["text"] == "Hello from tests"

    def test_message_status(self, client, event_store):
        """Status callback stores event and returns 200."""
        payload = [
            {
                "type": "message-delivered",
                "message": {"id": "msg-abc123"},
            }
        ]
        response = client.post("/callbacks/messaging/status", json=payload)
        assert response.status_code == 200

        events = event_store.get_events("messaging.status")
        assert len(events) == 1


class TestVoiceCallbacks:
    def test_answer_callback_returns_redirect(self, client, event_store):
        """Answer callback stores event and returns redirect BXML."""
        payload = {
            "eventType": "answer",
            "callId": "call-123",
            "from": "+19195551234",
            "to": "+19195554321",
            "applicationId": "app-1",
        }
        response = client.post("/callbacks/voice/answer", json=payload)
        assert response.status_code == 200
        assert "application/xml" in response.headers["content-type"]
        assert "<Redirect" in response.text
        assert "call-123" in response.text

        # Call state should be created
        call = event_store.get_call("call-123")
        assert call is not None
        assert call.from_number == "+19195551234"

    def test_gather_callback_stores_transcription(self, client, event_store):
        """Gather callback stores event with transcription and returns redirect."""
        # First create the call
        event_store.create_call("call-456", "+19195551234", "+19195554321", "app-1")

        payload = {
            "eventType": "gather",
            "callId": "call-456",
            "digits": "",
            "terminatingDigit": "",
            "speech": {"transcript": "I want to check my order", "confidence": 0.95},
        }
        response = client.post("/callbacks/voice/gather", json=payload)
        assert response.status_code == 200
        assert "<Redirect" in response.text

        call = event_store.get_call("call-456")
        assert len(call.turns) == 1
        assert call.turns[0]["role"] == "caller"
        assert "check my order" in call.turns[0]["text"]

    def test_disconnect_callback(self, client, event_store):
        """Disconnect callback cleans up call state."""
        event_store.create_call("call-789", "+19195551234", "+19195554321", "app-1")

        payload = {"eventType": "disconnect", "callId": "call-789", "cause": "hangup"}
        response = client.post("/callbacks/voice/disconnect", json=payload)
        assert response.status_code == 200

        assert event_store.get_call("call-789") is None

    def test_continue_returns_pending_bxml(self, client, event_store):
        """Continue endpoint returns queued BXML."""
        event_store.create_call("call-100", "+11111111111", "+12222222222", "app-1")
        call = event_store.get_call("call-100")
        call.pending_bxml = "<Response><SpeakSentence>Hello</SpeakSentence></Response>"

        response = client.post("/callbacks/voice/continue/call-100")
        assert response.status_code == 200
        assert "Hello" in response.text
        assert call.pending_bxml is None

    def test_continue_redirects_when_no_bxml(self, client, event_store):
        """Continue endpoint chains redirect when no BXML ready."""
        event_store.create_call("call-200", "+11111111111", "+12222222222", "app-1")

        response = client.post("/callbacks/voice/continue/call-200")
        assert response.status_code == 200
        assert "<Redirect" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test/test_callbacks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.callbacks'`

- [ ] **Step 3: Implement callback routes**

```python
# src/callbacks.py
"""Starlette callback routes for Bandwidth webhooks.

Messaging callbacks are fire-and-forget (store event, return 200).
Voice callbacks are stateful (store event, return BXML or redirect).
"""

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from event_store import EventStore

MAX_REDIRECT_CHAIN = 3


def _bxml_response(bxml: str) -> Response:
    return Response(content=bxml, media_type="application/xml")


def _redirect_bxml(call_id: str) -> str:
    return f'<Response><Redirect redirectUrl="/callbacks/voice/continue/{call_id}" /></Response>'


def create_callback_app(event_store: EventStore) -> Starlette:
    """Create a Starlette app with callback routes.

    Args:
        event_store: Shared event store for persisting callback events.

    Returns:
        Starlette app mountable on the main server.
    """

    async def messaging_inbound(request: Request) -> JSONResponse:
        payload = await request.json()
        for event in payload:
            key = event.get("message", {}).get("from", "unknown")
            event_store.push("messaging.inbound", key, event)
        return JSONResponse({"status": "ok"})

    async def messaging_status(request: Request) -> JSONResponse:
        payload = await request.json()
        for event in payload:
            key = event.get("message", {}).get("id", "unknown")
            event_store.push("messaging.status", key, event)
        return JSONResponse({"status": "ok"})

    async def voice_answer(request: Request) -> Response:
        payload = await request.json()
        call_id = payload.get("callId", "unknown")
        event_store.push("voice.answer", call_id, payload)

        event_store.create_call(
            call_id=call_id,
            from_number=payload.get("from", ""),
            to_number=payload.get("to", ""),
            application_id=payload.get("applicationId", ""),
        )

        return _bxml_response(_redirect_bxml(call_id))

    async def voice_gather(request: Request) -> Response:
        payload = await request.json()
        call_id = payload.get("callId", "unknown")
        event_store.push("voice.gather", call_id, payload)

        call = event_store.get_call(call_id)
        if call:
            speech = payload.get("speech", {})
            transcript = speech.get("transcript", "")
            digits = payload.get("digits", "")
            text = transcript or digits or "(no input)"
            call.add_turn("caller", text)

        return _bxml_response(_redirect_bxml(call_id))

    async def voice_disconnect(request: Request) -> JSONResponse:
        payload = await request.json()
        call_id = payload.get("callId", "unknown")
        event_store.push("voice.disconnect", call_id, payload)
        event_store.remove_call(call_id)
        return JSONResponse({"status": "ok"})

    async def voice_continue(request: Request) -> Response:
        call_id = request.path_params["call_id"]
        call = event_store.get_call(call_id)

        if call:
            bxml = call.consume_pending_bxml()
            if bxml:
                return _bxml_response(bxml)

        # No BXML ready — redirect again
        return _bxml_response(_redirect_bxml(call_id))

    routes = [
        Route("/callbacks/messaging/inbound", messaging_inbound, methods=["POST"]),
        Route("/callbacks/messaging/status", messaging_status, methods=["POST"]),
        Route("/callbacks/voice/answer", voice_answer, methods=["POST"]),
        Route("/callbacks/voice/gather", voice_gather, methods=["POST"]),
        Route("/callbacks/voice/disconnect", voice_disconnect, methods=["POST"]),
        Route("/callbacks/voice/continue/{call_id}", voice_continue, methods=["POST"]),
    ]

    return Starlette(routes=routes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test/test_callbacks.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/callbacks.py test/test_callbacks.py
git commit -m "feat: add Starlette callback routes for messaging and voice webhooks"
```

---

### Task 7: Callback MCP Tools

**Files:**
- Create: `src/tools/callbacks.py`
- Create: `test/test_callback_tools.py`

- [ ] **Step 1: Write failing tests**

```python
# test/test_callback_tools.py
import pytest
from fastmcp import FastMCP
from src.event_store import EventStore
from src.tools.callbacks import register_callback_tools


@pytest.fixture
def event_store():
    return EventStore(max_events=100, ttl_seconds=3600)


@pytest.fixture
def mcp_with_callbacks(event_store):
    mcp = FastMCP(name="Test")
    register_callback_tools(mcp, event_store)
    return mcp


@pytest.mark.asyncio
async def test_callback_tools_registered(mcp_with_callbacks):
    """Callback tools are registered on the MCP server."""
    tools = await mcp_with_callbacks.get_tools()
    assert "getInboundMessages" in tools
    assert "getCallbackEvents" in tools


@pytest.mark.asyncio
async def test_get_inbound_messages(event_store):
    """getInboundMessages returns stored messaging events."""
    from src.tools.callbacks import get_inbound_messages_flow

    event_store.push("messaging.inbound", "+19195551234", {"message": {"text": "hi", "from": "+19195551234"}})
    event_store.push("messaging.inbound", "+19195559999", {"message": {"text": "other", "from": "+19195559999"}})

    # All messages
    result = await get_inbound_messages_flow(event_store)
    assert len(result["events"]) == 2

    # Filtered by number
    result = await get_inbound_messages_flow(event_store, phone_number="+19195551234")
    assert len(result["events"]) == 1
    assert result["events"][0]["message"]["text"] == "hi"


@pytest.mark.asyncio
async def test_get_callback_events(event_store):
    """getCallbackEvents returns events filtered by type."""
    from src.tools.callbacks import get_callback_events_flow

    event_store.push("messaging.inbound", "+19195551234", {"type": "message-received"})
    event_store.push("voice.gather", "call-1", {"type": "gather"})

    result = await get_callback_events_flow(event_store, event_type="voice.gather")
    assert len(result["events"]) == 1
    assert result["events"][0]["type"] == "gather"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test/test_callback_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.tools.callbacks'`

- [ ] **Step 3: Implement callback tools**

```python
# src/tools/callbacks.py
"""MCP tools for reading callback events."""

from typing import Optional
from event_store import EventStore


async def get_inbound_messages_flow(
    event_store: EventStore,
    phone_number: Optional[str] = None,
    since: Optional[float] = None,
) -> dict:
    """Get inbound messaging events."""
    if phone_number:
        events = event_store.get_events("messaging.inbound", key=phone_number, since=since)
    else:
        events = event_store.get_events("messaging.inbound", since=since)

    # Strip internal fields from response
    cleaned = [{k: v for k, v in e.items() if not k.startswith("_")} for e in events]
    return {"events": cleaned, "count": len(cleaned)}


async def get_callback_events_flow(
    event_store: EventStore,
    event_type: Optional[str] = None,
    call_id: Optional[str] = None,
    phone_number: Optional[str] = None,
    since: Optional[float] = None,
) -> dict:
    """Get callback events with flexible filtering."""
    if call_id and event_type:
        events = event_store.get_events(event_type, key=call_id, since=since)
    elif phone_number and event_type:
        events = event_store.get_events(event_type, key=phone_number, since=since)
    elif event_type:
        events = event_store.get_events(event_type, since=since)
    else:
        # Collect across all known event types
        all_events = []
        for et in ["messaging.inbound", "messaging.status", "voice.answer", "voice.gather", "voice.disconnect"]:
            all_events.extend(event_store.get_events(et, since=since))
        all_events.sort(key=lambda e: e.get("_received_at", 0))
        events = all_events

    cleaned = [{k: v for k, v in e.items() if not k.startswith("_")} for e in events]
    return {"events": cleaned, "count": len(cleaned)}


def register_callback_tools(mcp, event_store: EventStore) -> None:
    """Register callback-reading tools on the MCP server."""

    @mcp.tool(name="getInboundMessages")
    async def get_inbound_messages(
        phone_number: Optional[str] = None,
        since: Optional[float] = None,
    ) -> dict:
        """Get recent inbound SMS/MMS messages received by your Bandwidth number.

        Args:
            phone_number: Filter by sender phone number (E.164 format).
            since: Only return events after this Unix timestamp.
        """
        return await get_inbound_messages_flow(event_store, phone_number, since)

    @mcp.tool(name="getCallbackEvents")
    async def get_callback_events(
        event_type: Optional[str] = None,
        call_id: Optional[str] = None,
        phone_number: Optional[str] = None,
        since: Optional[float] = None,
    ) -> dict:
        """Get callback events from Bandwidth webhooks.

        Filterable by event type (messaging.inbound, voice.gather, etc.),
        call ID, phone number, and timestamp.

        Args:
            event_type: Filter by event type (e.g. "messaging.inbound", "voice.gather").
            call_id: Filter voice events by call ID.
            phone_number: Filter by phone number.
            since: Only return events after this Unix timestamp.
        """
        return await get_callback_events_flow(event_store, event_type, call_id, phone_number, since)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test/test_callback_tools.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/tools/callbacks.py test/test_callback_tools.py
git commit -m "feat: add getInboundMessages and getCallbackEvents MCP tools"
```

---

### Task 8: BXML Generation Tool

**Files:**
- Create: `src/tools/voice.py`
- Create: `test/test_bxml.py`

- [ ] **Step 1: Write failing tests for BXML generation**

```python
# test/test_bxml.py
import pytest
from xml.etree.ElementTree import fromstring
from src.tools.voice import generate_bxml_flow


@pytest.mark.asyncio
async def test_speak_sentence():
    """SpeakSentence verb produces valid BXML."""
    result = await generate_bxml_flow([
        {"type": "SpeakSentence", "text": "Hello world"}
    ])
    assert "<SpeakSentence" in result
    assert "Hello world" in result
    # Should parse as valid XML
    fromstring(result)


@pytest.mark.asyncio
async def test_speak_with_voice():
    """SpeakSentence respects voice attribute."""
    result = await generate_bxml_flow([
        {"type": "SpeakSentence", "text": "Hi", "voice": "julie"}
    ])
    assert 'voice="julie"' in result


@pytest.mark.asyncio
async def test_gather_wrapping_speak():
    """Gather can wrap SpeakSentence for barge-in."""
    result = await generate_bxml_flow([
        {
            "type": "Gather",
            "input_type": "speech dtmf",
            "max_wait_time": 8,
            "speech_timeout": 2,
            "verbs": [
                {"type": "SpeakSentence", "text": "How can I help?"}
            ],
        }
    ])
    root = fromstring(result)
    gather = root.find("Gather")
    assert gather is not None
    assert gather.get("inputType") == "speech dtmf"
    speak = gather.find("SpeakSentence")
    assert speak is not None
    assert speak.text == "How can I help?"


@pytest.mark.asyncio
async def test_transfer():
    """Transfer verb with phone number."""
    result = await generate_bxml_flow([
        {"type": "Transfer", "transfer_to": "+19195551234"}
    ])
    root = fromstring(result)
    transfer = root.find("Transfer")
    assert transfer is not None
    phone = transfer.find("PhoneNumber")
    assert phone is not None
    assert phone.text == "+19195551234"


@pytest.mark.asyncio
async def test_hangup():
    """Hangup verb."""
    result = await generate_bxml_flow([{"type": "Hangup"}])
    assert "<Hangup" in result
    fromstring(result)


@pytest.mark.asyncio
async def test_pause():
    """Pause verb with duration."""
    result = await generate_bxml_flow([{"type": "Pause", "duration": 3}])
    root = fromstring(result)
    pause = root.find("Pause")
    assert pause is not None
    assert pause.get("duration") == "3"


@pytest.mark.asyncio
async def test_redirect():
    """Redirect verb."""
    result = await generate_bxml_flow([
        {"type": "Redirect", "redirect_url": "/callbacks/voice/continue/call-1"}
    ])
    root = fromstring(result)
    redirect = root.find("Redirect")
    assert redirect is not None
    assert redirect.get("redirectUrl") == "/callbacks/voice/continue/call-1"


@pytest.mark.asyncio
async def test_record():
    """Record verb."""
    result = await generate_bxml_flow([
        {"type": "Record", "max_duration": 60, "silence_timeout": 5}
    ])
    root = fromstring(result)
    record = root.find("Record")
    assert record is not None
    assert record.get("maxDuration") == "60"


@pytest.mark.asyncio
async def test_play_audio():
    """PlayAudio verb."""
    result = await generate_bxml_flow([
        {"type": "PlayAudio", "url": "https://example.com/audio.mp3"}
    ])
    root = fromstring(result)
    play = root.find("PlayAudio")
    assert play is not None
    assert play.text == "https://example.com/audio.mp3"


@pytest.mark.asyncio
async def test_send_dtmf():
    """SendDtmf verb."""
    result = await generate_bxml_flow([{"type": "SendDtmf", "digits": "1234#"}])
    root = fromstring(result)
    dtmf = root.find("SendDtmf")
    assert dtmf is not None
    assert dtmf.text == "1234#"


@pytest.mark.asyncio
async def test_multiple_verbs():
    """Multiple verbs in sequence."""
    result = await generate_bxml_flow([
        {"type": "SpeakSentence", "text": "Goodbye"},
        {"type": "Hangup"},
    ])
    root = fromstring(result)
    children = list(root)
    assert len(children) == 2
    assert children[0].tag == "SpeakSentence"
    assert children[1].tag == "Hangup"


@pytest.mark.asyncio
async def test_unknown_verb_raises():
    """Unknown verb type raises ValueError."""
    with pytest.raises(ValueError, match="Unknown BXML verb"):
        await generate_bxml_flow([{"type": "FlyToMoon"}])


@pytest.mark.asyncio
async def test_auto_gather_wrap():
    """Top-level SpeakSentence is auto-wrapped in Gather when auto_gather=True."""
    result = await generate_bxml_flow(
        [{"type": "SpeakSentence", "text": "Hello"}],
        auto_gather=True,
    )
    root = fromstring(result)
    # Should be Gather > SpeakSentence, not bare SpeakSentence
    gather = root.find("Gather")
    assert gather is not None
    assert gather.find("SpeakSentence") is not None


@pytest.mark.asyncio
async def test_xml_escaping():
    """Special characters in text are XML-escaped."""
    result = await generate_bxml_flow([
        {"type": "SpeakSentence", "text": 'Use <b> & "quotes"'}
    ])
    # Should parse without error (means it's properly escaped)
    fromstring(result)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test/test_bxml.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.tools.voice'`

- [ ] **Step 3: Implement BXML generation**

```python
# src/tools/voice.py
"""MCP tools for programmable voice: BXML generation and call response."""

from typing import Any, Optional
from xml.etree.ElementTree import Element, SubElement, tostring

from event_store import EventStore


def _snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase for BXML attributes."""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _build_verb(verb: dict[str, Any], parent: Element) -> None:
    """Build a BXML verb element and append to parent."""
    verb_type = verb.get("type")
    if not verb_type:
        raise ValueError("Each verb must have a 'type' field")

    if verb_type == "SpeakSentence":
        el = SubElement(parent, "SpeakSentence")
        el.text = verb.get("text", "")
        if "voice" in verb:
            el.set("voice", verb["voice"])
        if "locale" in verb:
            el.set("locale", verb["locale"])

    elif verb_type == "Gather":
        el = SubElement(parent, "Gather")
        for attr in ["input_type", "max_wait_time", "speech_timeout", "max_digits",
                      "inter_digit_timeout", "terminating_digits", "first_digit_timeout",
                      "repeat_count"]:
            if attr in verb:
                el.set(_snake_to_camel(attr), str(verb[attr]))
        for child_verb in verb.get("verbs", []):
            _build_verb(child_verb, el)

    elif verb_type == "Transfer":
        el = SubElement(parent, "Transfer")
        if "transfer_caller_id" in verb:
            el.set("transferCallerId", verb["transfer_caller_id"])
        phone = SubElement(el, "PhoneNumber")
        phone.text = verb.get("transfer_to", "")

    elif verb_type == "Hangup":
        SubElement(parent, "Hangup")

    elif verb_type == "Pause":
        el = SubElement(parent, "Pause")
        if "duration" in verb:
            el.set("duration", str(verb["duration"]))

    elif verb_type == "Redirect":
        el = SubElement(parent, "Redirect")
        if "redirect_url" in verb:
            el.set("redirectUrl", verb["redirect_url"])

    elif verb_type == "Record":
        el = SubElement(parent, "Record")
        for attr in ["max_duration", "silence_timeout", "callback_url",
                      "file_format", "transcribe"]:
            if attr in verb:
                el.set(_snake_to_camel(attr), str(verb[attr]))

    elif verb_type == "PlayAudio":
        el = SubElement(parent, "PlayAudio")
        el.text = verb.get("url", "")

    elif verb_type == "Ring":
        el = SubElement(parent, "Ring")
        if "duration" in verb:
            el.set("duration", str(verb["duration"]))

    elif verb_type == "SendDtmf":
        el = SubElement(parent, "SendDtmf")
        el.text = verb.get("digits", "")

    elif verb_type == "Bridge":
        el = SubElement(parent, "Bridge")
        el.set("targetCall", verb.get("target_call", ""))

    elif verb_type == "StartRecording":
        el = SubElement(parent, "StartRecording")
        if "callback_url" in verb:
            el.set("recordingAvailableUrl", verb["callback_url"])

    elif verb_type == "StopRecording":
        SubElement(parent, "StopRecording")

    elif verb_type == "StartTranscription":
        el = SubElement(parent, "StartTranscription")
        if "callback_url" in verb:
            el.set("transcriptionAvailableUrl", verb["callback_url"])
        if "tracks" in verb:
            el.set("tracks", verb["tracks"])

    elif verb_type == "StopTranscription":
        SubElement(parent, "StopTranscription")

    else:
        raise ValueError(f"Unknown BXML verb: '{verb_type}'")


async def generate_bxml_flow(
    verbs: list[dict[str, Any]],
    auto_gather: bool = False,
) -> str:
    """Generate valid BXML from a list of verb descriptions.

    Args:
        verbs: List of verb dicts, each with 'type' and verb-specific fields.
        auto_gather: If True, wrap top-level SpeakSentence in Gather for barge-in.

    Returns:
        Valid BXML string.
    """
    root = Element("Response")

    for verb in verbs:
        if auto_gather and verb.get("type") == "SpeakSentence":
            gather_verb = {
                "type": "Gather",
                "max_wait_time": 8,
                "speech_timeout": 2,
                "input_type": "speech dtmf",
                "verbs": [verb],
            }
            _build_verb(gather_verb, root)
        else:
            _build_verb(verb, root)

    return tostring(root, encoding="unicode", xml_declaration=False)


async def respond_to_callback_flow(
    event_store: EventStore,
    call_id: str,
    bxml: str,
) -> dict:
    """Queue BXML for an active call. First-write-wins."""
    call = event_store.get_call(call_id)
    if not call:
        return {"error": "call_not_found", "call_id": call_id}

    if not call.try_set_bxml(bxml):
        return {"error": "already_handled", "call_id": call_id}

    # Record agent's turn from BXML content (best-effort text extraction)
    call.add_turn("agent", "(BXML response queued)")

    return {"status": "queued", "call_id": call_id}


def register_voice_tools(mcp, event_store: EventStore) -> None:
    """Register voice/BXML tools on the MCP server."""

    @mcp.tool(name="generateBXML")
    async def generate_bxml(
        verbs: list[dict[str, Any]],
        auto_gather: bool = True,
    ) -> str:
        """Generate valid Bandwidth XML (BXML) from verb descriptions.

        Each verb is a dict with 'type' and type-specific fields. Supported types:
        SpeakSentence, Gather, Transfer, PlayAudio, Record, Pause, Hangup,
        Redirect, Bridge, Ring, SendDtmf, StartRecording, StopRecording,
        StartTranscription, StopTranscription.

        When auto_gather is True (default), top-level SpeakSentence verbs are
        wrapped in Gather for barge-in support (caller can interrupt).

        Args:
            verbs: List of verb descriptions.
            auto_gather: Wrap SpeakSentence in Gather for barge-in. Default True.
        """
        return await generate_bxml_flow(verbs, auto_gather)

    @mcp.tool(name="respondToCallback")
    async def respond_to_callback(call_id: str, bxml: str) -> dict:
        """Queue a BXML response for an active voice call.

        Use after reading a gather result from getCallbackEvents and generating
        BXML via generateBXML. The next redirect for this call will deliver the BXML.

        First-write-wins: if another session already queued BXML for this call,
        this call returns an error instead of overwriting.

        Args:
            call_id: The call ID to respond to.
            bxml: Valid BXML string (use generateBXML to produce this).
        """
        return await respond_to_callback_flow(event_store, call_id, bxml)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test/test_bxml.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/tools/voice.py test/test_bxml.py
git commit -m "feat: add generateBXML and respondToCallback voice tools"
```

---

### Task 9: Transport Config & App Wiring

**Files:**
- Modify: `src/config.py`
- Modify: `src/app.py`

- [ ] **Step 1: Add transport config to config.py**

Add to `src/config.py`:

```python
# Add to load_config(), after the all_vars loop:
    # Transport config
    transport_vars = [
        "BW_MCP_TRANSPORT",
        "BW_MCP_HOST",
        "BW_MCP_PORT",
        "BW_MCP_AUTH_TOKEN",
        "BW_MCP_BASE_URL",
        "BW_VOICE_FALLBACK_NUMBER",
    ]
    for var in transport_vars:
        value = os.environ.get(var)
        if value:
            config[var] = value

# Add to _parse_cli_args(), after --exclude-tools:
    parser.add_argument(
        "--transport",
        help="Transport type: stdio (default), sse, or streamable-http.",
        type=str,
        choices=["stdio", "sse", "streamable-http"],
    )
    parser.add_argument(
        "--port",
        help="Port for HTTP transport (default: 8080).",
        type=int,
    )

# Add new function:
def get_transport_config() -> dict:
    """Get transport configuration from CLI args and env vars."""
    args = _parse_cli_args()
    return {
        "transport": args.transport or os.getenv("BW_MCP_TRANSPORT", "stdio"),
        "host": os.getenv("BW_MCP_HOST", "0.0.0.0"),
        "port": args.port or int(os.getenv("BW_MCP_PORT", "8080")),
    }
```

- [ ] **Step 2: Wire everything together in app.py**

Update `src/app.py` to mount callbacks and use transport config:

```python
import asyncio
import os
import warnings

os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

from fastmcp import FastMCP
from servers import create_bandwidth_mcp, api_server_info, _create_server
from config import load_config, get_enabled_tools, get_excluded_tools, get_transport_config
from server_utils import create_route_map_fn
from tools.credentials import register_credentials_tools
from tools.callbacks import register_callback_tools
from tools.voice import register_voice_tools
from instructions import build_instructions
from event_store import EventStore
from callbacks import create_callback_app

mcp = FastMCP(name="Bandwidth MCP")
_config = {}
_event_store = EventStore()


async def _reload_authenticated_servers():
    """Load authenticated API servers after credentials are set mid-session."""
    if _config.get("_authenticated_servers_loaded"):
        return
    _config["_authenticated_servers_loaded"] = True

    enabled_tools = get_enabled_tools()
    excluded_tools = get_excluded_tools()
    route_map_fn = create_route_map_fn(enabled_tools, excluded_tools)

    for api_name, api_info in api_server_info.items():
        requires_auth = api_info.get("requires_auth", True)
        if not requires_auth:
            continue
        try:
            server = await _create_server(
                url=api_info["url"],
                route_map_fn=route_map_fn,
                config=_config,
                requires_auth=True,
            )
            await mcp.import_server(server)
        except Exception as e:
            warnings.warn(f"Failed to load {api_name} after credential update: {e}")

    # Rebuild instructions with newly loaded tools
    all_tools = await mcp.get_tools()
    mcp.instructions = build_instructions(_config, list(all_tools.keys()))


async def setup(mcp: FastMCP = mcp):
    """Setup the Bandwidth MCP server with tools and resources."""
    global _config
    enabled_tools = get_enabled_tools()
    excluded_tools = get_excluded_tools()
    _config = load_config()

    print("Setting up Bandwidth MCP server...")
    await create_bandwidth_mcp(mcp, enabled_tools, excluded_tools, _config)

    register_credentials_tools(mcp, _config, reload_callback=_reload_authenticated_servers)
    register_callback_tools(mcp, _event_store)
    register_voice_tools(mcp, _event_store)

    # Build and set instructions based on loaded tools
    all_tools = await mcp.get_tools()
    mcp.instructions = build_instructions(_config, list(all_tools.keys()))


def main():
    """Main function to run the Bandwidth MCP server."""
    asyncio.run(setup())

    transport_config = get_transport_config()
    transport = transport_config["transport"]

    if transport == "stdio":
        mcp.run()
    else:
        # Mount callback routes for HTTP transports
        callback_app = create_callback_app(_event_store)
        mcp.mount("callbacks", callback_app)
        mcp.run(
            transport=transport,
            host=transport_config["host"],
            port=transport_config["port"],
        )


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run full test suite**

Run: `pytest -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/config.py src/app.py
git commit -m "feat: wire transport config, callbacks, voice tools, and instructions into app"
```

---

### Task 10: Update AGENTS.md and Documentation

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`

- [ ] **Step 1: Add new capabilities to AGENTS.md**

Add the following sections to `AGENTS.md` after the existing "Credentials" section:

```markdown
### Callback Events (built-in tools)

| Tool | Description |
|---|---|
| `getInboundMessages` | Get recent inbound SMS/MMS events. Filterable by phone number and timestamp. |
| `getCallbackEvents` | Get all callback events (voice + messaging), filterable by type, call ID, phone number. |

These tools read from the server's event store. Events are populated by Bandwidth webhooks when the server runs in hosted HTTP mode with callbacks configured.

---

### Voice & BXML (built-in tools)

| Tool | Description |
|---|---|
| `generateBXML` | Generate valid BXML from verb descriptions. Auto-wraps SpeakSentence in Gather for barge-in. |
| `respondToCallback` | Queue a BXML response for an active voice call. First-write-wins for multi-session safety. |

#### Voice Call Flow

1. Ensure a voice application is configured with callback URLs pointing at this server.
2. Call `createCall` to initiate, or receive an inbound call.
3. Call `getCallbackEvents` to read voice events (gather results with transcribed speech).
4. Call `generateBXML` to build the next response.
5. Call `respondToCallback` to deliver the BXML to the active call.

Supported BXML verbs: SpeakSentence, Gather, Transfer, PlayAudio, Record, Pause, Hangup, Redirect, Bridge, Ring, SendDtmf, StartRecording, StopRecording, StartTranscription, StopTranscription.
```

Add to the "Common Workflows" section:

```markdown
### Receive and Reply to an SMS

Prerequisites: Hosted HTTP mode, `BW_MCP_BASE_URL` configured, callbacks configured on application.

1. Call `getInboundMessages` to check for new messages.
2. Read the sender's number and message text.
3. Call `createMessage` with `to` set to the sender's number.

### Handle a Voice Call

Prerequisites: Hosted HTTP mode, voice application with callback URLs pointing at this server.

1. Call `getCallbackEvents(event_type="voice.gather")` to read caller input.
2. Call `generateBXML` with the verbs to speak and gather the next input.
3. Call `respondToCallback` with the call ID and BXML.
4. Repeat until the call ends.
```

- [ ] **Step 2: Add hosting section to README.md**

Add a "Hosted Mode" section to README.md after the existing usage section:

```markdown
## Hosted Mode

Run the server over HTTP to enable remote access and webhook callbacks:

```bash
BW_MCP_TRANSPORT=streamable-http \
BW_MCP_PORT=8080 \
BW_MCP_BASE_URL=https://your-server.example.com \
BW_USERNAME=your_username \
BW_PASSWORD=your_password \
BW_ACCOUNT_ID=your_account_id \
python src/app.py
```

### Tool Profiles

Reduce context window pressure with named presets:

```bash
BW_MCP_PROFILE=messaging    # SMS/MMS tools only
BW_MCP_PROFILE=voice        # Voice + BXML tools
BW_MCP_PROFILE=onboarding   # Account creation
BW_MCP_PROFILE=lookup        # Number intelligence
BW_MCP_PROFILE=messaging,voice  # Combine profiles
```
```

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md README.md
git commit -m "docs: update AGENTS.md and README with callbacks, voice, hosting, and profiles"
```

---

### Task 11: Integration Test

**Files:**
- Create: `test/test_integration.py`

- [ ] **Step 1: Write integration test for end-to-end flow**

```python
# test/test_integration.py
"""Integration tests verifying the full MCP server setup."""

import pytest
from fastmcp import FastMCP
from pytest_httpx import HTTPXMock
from utils import create_mock
from src.app import setup, mcp, _event_store


@pytest.fixture(autouse=True)
def reset_mcp():
    """Reset mcp state between tests."""
    # Clear any tools from previous test runs
    yield


@pytest.mark.asyncio
async def test_instructions_set_after_setup(httpx_mock: HTTPXMock):
    """After setup, mcp.instructions is set and contains relevant content."""
    for name in [
        "messaging", "multi-factor-auth", "phone-number-lookup-v2",
        "insights", "end-user-management", "express",
    ]:
        create_mock(httpx_mock, name)

    test_mcp = FastMCP(name="Integration Test")
    await setup(test_mcp)

    assert test_mcp.instructions is not None
    assert "Bandwidth MCP Server" in test_mcp.instructions
    assert "createMessage" in test_mcp.instructions


@pytest.mark.asyncio
async def test_callback_tools_available_after_setup(httpx_mock: HTTPXMock):
    """Callback and voice tools are registered after setup."""
    for name in [
        "messaging", "multi-factor-auth", "phone-number-lookup-v2",
        "insights", "end-user-management", "express",
    ]:
        create_mock(httpx_mock, name)

    test_mcp = FastMCP(name="Integration Test")
    await setup(test_mcp)

    tools = await test_mcp.get_tools()
    assert "getInboundMessages" in tools
    assert "getCallbackEvents" in tools
    assert "generateBXML" in tools
    assert "respondToCallback" in tools
    assert "setCredentials" in tools
```

- [ ] **Step 2: Run integration test**

Run: `pytest test/test_integration.py -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add test/test_integration.py
git commit -m "test: add integration tests for full MCP server setup"
```
