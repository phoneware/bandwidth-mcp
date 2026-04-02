# MCP Server v2 — Hosted, Conversational, Agent-Native

**Date:** 2026-04-02
**Author:** Kush Shah + Claude
**Status:** Draft
**Builds on:** [Bandwidth Agent Platform Design](2026-03-27-bandwidth-agent-platform-design.md)

## BLUF

The MCP server today is a good API wrapper running over stdio. To reach parity with the CLI agent experience, it needs three things: (1) server-level instructions so LLM clients automatically know how to use the tools without reading resources, (2) an HTTP transport so it can be hosted remotely, and (3) a callback server on that same HTTP process so agents can receive inbound events — enabling real two-way conversations over voice and messaging.

This spec covers all three, plus programmable voice via BXML and the creative workarounds that make it feel conversational rather than robotic.

---

## Design Principles

- **The server teaches the agent how to use it.** An LLM connecting for the first time should behave as well as one that's read AGENTS.md cover-to-cover. This happens through the MCP `instructions` field, not resources.
- **One process, two roles.** The MCP server handles both outbound tool calls (agent → Bandwidth) and inbound events (Bandwidth → agent). No separate callback service.
- **Graceful degradation everywhere.** Slow LLM? Redirect buys time. Speech recognition fails? DTMF fallback. Spec URL down? Cached copy. Dead call? Transfer to a human.
- **Stdio and HTTP are both first-class.** Local dev uses stdio. Hosted deployment uses HTTP/SSE. Same codebase, one config flag.

---

## 1. Instructions-Based Agent Routing

### Problem

The CLI experience is good because AGENTS.md gets loaded into the agent's context. The MCP server serves AGENTS.md as a `resource://`, but most clients (Claude Desktop, Cursor, etc.) don't proactively read resources. The agent flies blind — it sees 50+ tools with raw OpenAPI descriptions and has to guess which ones to use.

### Solution

FastMCP's constructor accepts an `instructions` parameter:

```python
mcp = FastMCP(
    name="Bandwidth MCP",
    instructions="..."  # Injected into every client's LLM context automatically
)
```

This string is sent during the MCP initialization handshake and automatically injected into the LLM's context by conforming clients. No resource reading required.

### Instructions Content

The instructions should be a condensed, agent-optimized version of AGENTS.md — not a copy-paste. Optimized for:

- **Tool selection:** "If the user wants to send a text, use `createMessage`. If they want to look up a number, use `createLookup` then poll `getLookupStatus`."
- **Prerequisites:** "Most tools require `BW_ACCOUNT_ID`. Check `resource://config` first, or call `setCredentials` if nothing is configured."
- **Workflows:** Sequenced multi-tool flows (send SMS, register account, look up number, etc.)
- **Error recovery:** What to do when things fail.
- **What NOT to do:** "Don't call authenticated tools before checking credentials. Don't treat a 'pending' lookup response as a failure."

The full AGENTS.md stays as a resource for clients that want the deep reference. The instructions are the fast path.

### Instructions Structure

```
# Bandwidth MCP Server

You have access to Bandwidth's communication APIs. Here's how to use them.

## Before You Start
- Read resource://config to check what credentials are loaded.
- If no credentials: use Express Registration (createRegistration → sendVerificationCode → 
  verifyRegistrationCode → setCredentials) to create an account first.

## Sending Messages (SMS/MMS)
Requires: BW_ACCOUNT_ID, BW_MESSAGING_APPLICATION_ID, BW_NUMBER
- createMessage: Send an SMS or MMS. Needs `to`, `from` (your BW_NUMBER), 
  `applicationId` (BW_MESSAGING_APPLICATION_ID), and `text`.
- listMessages: Check delivery status or search message history.

## Phone Number Lookup
Requires: BW_ACCOUNT_ID
- createLookup → getLookupStatus: Async. Create the lookup, then poll status 
  with the returned requestId until complete.

## Multi-Factor Authentication
Requires: BW_ACCOUNT_ID, BW_NUMBER, application ID for chosen channel
- generateMessagingCode / generateVoiceCode → verifyCode: Send code, then verify it.

## Voice Calls
Requires: BW_ACCOUNT_ID, voice application with callback URL configured
- createCall: Initiate an outbound call.
- getCallbackEvents: Read inbound call events and transcribed speech.
- generateBXML: Produce BXML to control call flow (speak, gather input, transfer, record).
- Always wrap SpeakSentence in Gather for barge-in support.

## Reporting
Requires: BW_ACCOUNT_ID
- createReport → getReportStatus → getReportFile: Async report generation.

## Error Patterns
- 401: Wrong credentials. Check BW_USERNAME/BW_PASSWORD.
- 422: Missing or malformed fields. Phone numbers must be E.164 (+19195551234).
- "Tool not found": Check BW_MCP_TOOLS / BW_MCP_EXCLUDE_TOOLS filters.
- "Pending" responses: Lookup and reporting are async. Poll, don't fail.
```

### Implementation

- New file: `src/instructions.py` — builds the instructions string dynamically based on which tools are actually loaded (no point documenting messaging tools if they're filtered out)
- `app.py` passes the instructions to `FastMCP(instructions=build_instructions(config, loaded_tools))`
- Instructions are regenerated when `setCredentials` triggers a reload

### Dynamic Instructions

The instructions string adapts to the server's actual state:

```python
def build_instructions(config: dict, loaded_tools: list[str]) -> str:
    sections = [HEADER]
    
    if "createMessage" in loaded_tools:
        sections.append(MESSAGING_SECTION)
    if "createLookup" in loaded_tools:
        sections.append(LOOKUP_SECTION)
    if "createCall" in loaded_tools:
        sections.append(VOICE_SECTION)
    # ... etc
    
    if not config.get("BW_USERNAME"):
        sections.insert(1, NO_CREDENTIALS_WARNING)
    
    return "\n\n".join(sections)
```

This means an agent connecting to a messaging-only server gets messaging instructions, not a wall of irrelevant voice documentation.

---

## 2. HTTP Transport & Hosting

### Problem

The server currently runs stdio — it's a subprocess spawned by the client. This means:
- Can't receive inbound webhooks (no public URL)
- Can't be shared across clients
- Can't be deployed as a service

### Solution

FastMCP supports multiple transports. Switch with one parameter:

```python
# Local development (unchanged)
mcp.run()  # stdio, default

# Hosted deployment
mcp.run(transport="streamable-http", host="0.0.0.0", port=8080)
```

Under the hood, FastMCP runs on Starlette/uvicorn. The `streamable-http` transport exposes the MCP protocol over HTTP with server-sent events for streaming.

### Transport Configuration

New env var: `BW_MCP_TRANSPORT`

| Value | Behavior | Use Case |
|-------|----------|----------|
| `stdio` (default) | Subprocess, JSON-RPC over stdin/stdout | Local dev, Claude Desktop, Cursor |
| `sse` | HTTP + Server-Sent Events | Hosted, legacy MCP clients |
| `streamable-http` | HTTP + streaming (MCP spec preferred) | Hosted, modern MCP clients |

Additional env vars for hosted mode:

| Var | Default | Description |
|-----|---------|-------------|
| `BW_MCP_HOST` | `0.0.0.0` | Bind address |
| `BW_MCP_PORT` | `8080` | Bind port |
| `BW_MCP_AUTH_TOKEN` | None | Bearer token for MCP endpoint auth (required in hosted mode) |
| `BW_MCP_BASE_URL` | Auto-detected | Public URL of the server (used for callback URL registration) |

### MCP Endpoint Auth

When hosted, the MCP endpoint needs its own auth layer (separate from Bandwidth API auth). A simple bearer token:

```python
# Client connects with:
# Authorization: Bearer <BW_MCP_AUTH_TOKEN>
```

FastMCP's auth middleware validates the token before allowing tool calls. This prevents unauthorized access to the hosted server.

### Deployment

The server is a standard Python ASGI app. Deploy anywhere:

- **Fly.io / Railway:** `fly deploy` with a Dockerfile. Free TLS.
- **Cloud Run:** Container-based, scales to zero.
- **VPS:** `uvicorn app:mcp.asgi_app --host 0.0.0.0 --port 8080` behind nginx/caddy for TLS.
- **Dev/ngrok:** `ngrok http 8080` for local testing with a public URL.

Dockerfile (minimal):

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml requirements.txt ./
RUN pip install -r requirements.txt
COPY src/ ./
CMD ["python", "app.py"]
```

Env vars are passed at deploy time. No config files to manage.

---

## 3. Callback Server

### Problem

The server can make API calls but can't receive events. When someone texts your Bandwidth number or calls it, the event goes to a webhook URL — and there's nothing listening. AGENTS.md explicitly lists "No webhook registration" as a limitation.

### Solution

Since FastMCP sits on Starlette, mount additional HTTP routes on the same process. The MCP server becomes both the tool server and the callback receiver.

### Architecture

```
┌───────────────────────────────────────────────┐
│              Hosted MCP Server                 │
│                                                │
│  /mcp/        ← Streamable HTTP (LLM client)  │
│  /callbacks/  ← Bandwidth webhook POSTs        │
│                                                │
│  ┌──────────────┐  ┌───────────────────────┐  │
│  │  MCP Layer   │  │  Callback Router      │  │
│  │              │  │                       │  │
│  │  Tools,      │  │  /messaging/inbound   │  │
│  │  Resources,  │  │  /messaging/status    │  │
│  │  Instructions│  │  /voice/answer        │  │
│  │              │  │  /voice/gather        │  │
│  │              │  │  /voice/disconnect    │  │
│  │              │  │  /voice/transfer      │  │
│  │              │  │  /voice/recording     │  │
│  └──────┬───────┘  └──────────┬────────────┘  │
│         │                     │                │
│  ┌──────┴─────────────────────┴────────────┐  │
│  │            Event Store                   │  │
│  │                                          │  │
│  │  In-memory ring buffer (MVP)             │  │
│  │  Redis / SQLite (production)             │  │
│  │                                          │  │
│  │  Keyed by: type, phone number, call ID   │  │
│  │  TTL: configurable, default 1 hour       │  │
│  └──────────────────────────────────────────┘  │
└───────────────────────────────────────────────┘
```

### Callback Routes

All routes accept POST with JSON body (Bandwidth's webhook format).

| Route | Event Type | Response |
|-------|-----------|----------|
| `/callbacks/messaging/inbound` | Inbound SMS/MMS | 200 OK (ack) |
| `/callbacks/messaging/status` | Delivery status update | 200 OK (ack) |
| `/callbacks/voice/answer` | Call answered | BXML (see Voice section) |
| `/callbacks/voice/gather` | Gather completed (speech/DTMF) | BXML |
| `/callbacks/voice/disconnect` | Call ended | 200 OK (ack) |
| `/callbacks/voice/transfer` | Transfer event | BXML or 200 OK |
| `/callbacks/voice/recording` | Recording available | 200 OK (ack) |

Messaging callbacks are fire-and-forget (store event, return 200). Voice callbacks are synchronous — Bandwidth expects BXML back.

### Event Store

A ring buffer per event type, keyed by identifiers (phone number, call ID):

```python
class EventStore:
    def __init__(self, max_events=1000, ttl_seconds=3600):
        self._events: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_events))
        self._ttl = ttl_seconds
    
    def push(self, event_type: str, key: str, event: dict):
        """Store an event. Key is phone number, call ID, etc."""
        event["_received_at"] = time.time()
        self._events[f"{event_type}:{key}"].append(event)
    
    def get(self, event_type: str, key: str = None, since: float = None) -> list[dict]:
        """Retrieve events, optionally filtered by key and timestamp."""
        # ... filter by TTL, key, since timestamp
```

MVP uses in-memory storage. Production can swap to Redis or SQLite — the interface stays the same.

### New Tools for Callbacks

| Tool | Description |
|------|-------------|
| `getInboundMessages` | Get recent inbound SMS/MMS events. Filterable by phone number and timestamp. |
| `getMessageStatus` | Get delivery status events for sent messages. |
| `getCallbackEvents` | Get all callback events (voice + messaging), filterable by type, call ID, phone number. |
| `configureCallbacks` | Set the callback URLs on a Bandwidth application to point at this server. Self-configuring. |

`configureCallbacks` is the self-wiring tool — the agent calls it and the server registers its own public URL as the webhook destination:

```python
@mcp.tool()
async def configureCallbacks(application_id: str, types: list[str] = ["messaging", "voice"]):
    """Configure a Bandwidth application's callback URLs to point at this server.
    
    Args:
        application_id: The Bandwidth application ID to configure.
        types: Which callback types to register. Default: both messaging and voice.
    """
    base_url = config["BW_MCP_BASE_URL"]  # e.g., https://my-server.fly.dev
    
    update = {}
    if "messaging" in types:
        update["inbound_callback_url"] = f"{base_url}/callbacks/messaging/inbound"
        update["status_callback_url"] = f"{base_url}/callbacks/messaging/status"
    if "voice" in types:
        update["answer_url"] = f"{base_url}/callbacks/voice/answer"
        update["disconnect_url"] = f"{base_url}/callbacks/voice/disconnect"
    
    await bandwidth_api.update_application(application_id, update)
    return {"status": "configured", "base_url": base_url, "types": types}
```

---

## 4. Programmable Voice & BXML

### How Bandwidth Voice Works

Voice is turn-based over HTTP. Bandwidth manages the actual audio; the server tells it what to do via BXML (Bandwidth XML). Each turn:

1. Bandwidth POSTs a callback (call answered, gather completed, etc.)
2. Server responds with BXML (speak, gather, transfer, record, etc.)
3. Bandwidth executes the BXML
4. Next event triggers next callback

The agent never touches raw audio. It reads transcribed text and decides what to say.

### Voice Callback Flow

```
Inbound call
  → Bandwidth POSTs to /callbacks/voice/answer
  → Server generates greeting BXML with Gather
  → Caller speaks
  → Bandwidth transcribes, POSTs to /callbacks/voice/gather
  → Server reads transcription, generates response BXML
  → Repeat until call ends
```

### The Agent's Role in Voice

For voice callbacks, the server needs to produce BXML. There are two modes:

**Mode 1: Template-based (no LLM in the loop)**

Pre-configured BXML templates handle the call. Good for IVR flows, status checks, simple routing. The templates are stored in the event store and configured via a tool:

```python
@mcp.tool()
async def setVoiceHandler(
    application_id: str,
    greeting: str,
    gather_prompt: str,
    on_gather: dict  # mapping of keywords/DTMF to BXML responses
):
    """Configure how this server handles voice calls for an application."""
```

**Mode 2: Agent-in-the-loop (LLM generates BXML per turn)**

The callback handler stores the event and returns a `<Redirect>` to buy time. The agent polls `getCallbackEvents`, reads the transcription, calls `generateBXML` to produce the next response, and calls `respondToCallback` to deliver it.

This mode has higher latency but enables genuinely dynamic conversations.

```
Gather callback arrives
  → Server stores event, responds with <Redirect> to /callbacks/voice/continue/{callId}
  → Agent polls getCallbackEvents, sees new gather result
  → Agent calls generateBXML with the next thing to say
  → Agent calls respondToCallback(callId, bxml)
  → /callbacks/voice/continue/{callId} returns the queued BXML
```

### BXML Generation Tool

```python
@mcp.tool()
async def generateBXML(verbs: list[dict]) -> str:
    """Generate valid Bandwidth XML (BXML) from a list of verb descriptions.
    
    Each verb is a dict with 'type' and verb-specific fields.
    
    Supported verbs:
    - SpeakSentence: {type, text, voice?, locale?}
    - Gather: {type, input_type?, max_wait_time?, speech_timeout?, verbs?}
    - Transfer: {type, transfer_to, transfer_caller_id?}
    - PlayAudio: {type, url}
    - Record: {type, max_duration?, silence_timeout?, callback_url?}
    - Pause: {type, duration?}
    - Hangup: {type}
    - Redirect: {type, redirect_url}
    - Bridge: {type, target_call}
    - Ring: {type, duration?}
    - SendDtmf: {type, digits}
    - StartRecording: {type, callback_url?}
    - StopRecording: {type}
    - StartTranscription: {type, callback_url?, tracks?}
    - StopTranscription: {type}
    
    Example:
        generateBXML([
            {"type": "Gather", "input_type": "speech dtmf", "max_wait_time": 8, 
             "speech_timeout": 2, "verbs": [
                {"type": "SpeakSentence", "text": "How can I help you?", "voice": "julie"}
            ]},
        ])
    
    Returns valid BXML string.
    """
```

The tool handles:
- XML construction and escaping
- Nesting (Gather wrapping SpeakSentence/PlayAudio)
- SSML injection for natural-sounding speech (pauses, emphasis)
- Validation (reject invalid verb combinations)

### Design Rule: Always Gather-Wrap

Every `SpeakSentence` should be wrapped in a `Gather` by default. This enables barge-in — the caller can interrupt the TTS by speaking, and Bandwidth captures their input. The `generateBXML` tool enforces this unless explicitly overridden.

This single pattern transforms the experience from "robotic IVR" to "conversational."

### Voice Workarounds

#### Latency: Redirect Buffer

When a gather callback arrives and the agent needs time to think, respond with a bare `<Redirect>`:

```xml
<Response>
  <Redirect redirectUrl="/callbacks/voice/continue/{callId}" />
</Response>
```

Bandwidth's round-trip to the redirect URL takes ~500ms-1s. The agent has that window to generate BXML. If it's not ready yet, chain another redirect (up to a limit). No filler words — just the natural network latency as buffer.

Redirect chain budget: 3 max. If the agent still isn't ready after 3 redirects (~2-3 seconds), fall back to a template response.

#### Speech Recognition: Confirm and Fallback

For structured input (account numbers, dates, names), the agent should:

1. Ask for one piece of information at a time (short-turn bias)
2. Confirm what it heard: "I heard account 7-7-4-2. Is that right?"
3. Offer DTMF fallback: `input_type: "speech dtmf"` on every Gather

For free-form conversation, accept the ASR result and move on — confirmation loops for every sentence would be painful.

#### Long Calls: Raw Event Storage

The server stores all callback events as-is — no summarization, no compression. The LLM client manages its own context window and decides what's relevant. The event store is a dumb buffer, not an editorial layer.

`getCallbackEvents` returns raw turns for a call. If the LLM needs to trim context, that's its job.

#### Graceful Degradation: Transfer Fallback

If anything goes wrong mid-call — LLM timeout, repeated ASR failures, error from BXML generation — the fallback is always:

```xml
<Response>
  <SpeakSentence>I'm having some trouble. Let me connect you with someone who can help.</SpeakSentence>
  <Transfer transferTo="+19195551234" />
</Response>
```

The fallback transfer number is configurable via `BW_VOICE_FALLBACK_NUMBER`. If not set, hang up gracefully with an apology. Never leave a caller in dead air.

---

## 5. Tool Profiles

### Problem

50+ tools today, growing with voice and callbacks. Context window pressure degrades agent performance.

### Solution

Named presets for `BW_MCP_TOOLS`:

| Profile | Tools Included | Use Case |
|---------|---------------|----------|
| `messaging` | createMessage, listMessages, getInboundMessages, getMessageStatus, configureCallbacks | SMS/MMS send and receive |
| `voice` | createCall, generateBXML, getCallbackEvents, respondToCallback, configureCallbacks | Voice calls and BXML |
| `onboarding` | createRegistration, sendVerificationCode, verifyRegistrationCode, setCredentials | Account creation |
| `lookup` | createLookup, getLookupStatus | Number intelligence |
| `full` | All tools | Everything (power users) |

New env var: `BW_MCP_PROFILE`

```bash
BW_MCP_PROFILE=messaging  # Equivalent to BW_MCP_TOOLS=createMessage,listMessages,...
```

Profiles can be combined: `BW_MCP_PROFILE=messaging,voice`

`BW_MCP_TOOLS` and `BW_MCP_EXCLUDE_TOOLS` still work and override profiles for fine-tuning.

The instructions string adapts to the active profile — only documents the tools that are loaded.

---

## 6. Local Spec Cache

### Problem

If dev.bandwidth.com is unreachable at startup, tools don't load. No fallback.

### Solution

Cache specs locally after each successful fetch. Fall back to cache on failure.

```python
CACHE_DIR = Path("~/.bw-mcp/spec-cache").expanduser()

async def fetch_openapi_spec(url: str) -> dict:
    try:
        spec = await _fetch_from_url(url)
        _save_to_cache(url, spec)
        return spec
    except Exception:
        cached = _load_from_cache(url)
        if cached:
            warnings.warn(f"Using cached spec for {url}")
            return cached
        raise  # No cache, no network — fail loud
```

Cache location: `~/.bw-mcp/spec-cache/` (XDG-friendly alternative: `$XDG_CACHE_HOME/bw-mcp/`).

Specs are keyed by URL hash. No TTL — always prefer fresh, fall back to stale. A stale spec is infinitely better than no spec.

---

## 7. Call State Management

### Per-Call Conversation Buffer

Each active call gets a conversation buffer keyed by call ID:

```python
@dataclass
class CallState:
    call_id: str
    from_number: str
    to_number: str
    application_id: str
    started_at: float
    turns: list[dict]          # {"role": "caller"|"agent", "text": str, "timestamp": float}
    pending_bxml: str | None   # BXML queued by agent, waiting for redirect
    metadata: dict             # Arbitrary agent-set metadata
```

Lifecycle:
1. **Created** on first voice callback for a call ID
2. **Updated** on each gather/speak turn
3. **Archived** on disconnect callback (moved to cold storage or discarded)
4. **Expired** after TTL if no disconnect received (abandoned calls)

### Responding to Voice Callbacks

New tool for the agent-in-the-loop mode:

```python
@mcp.tool()
async def respondToCallback(call_id: str, bxml: str):
    """Queue a BXML response for an active call.
    
    Use after reading a gather result from getCallbackEvents and generating
    BXML via generateBXML. The next redirect for this call will return this BXML.
    
    Args:
        call_id: The call to respond to.
        bxml: Valid BXML string (use generateBXML to produce this).
    """
    call_state = event_store.get_call(call_id)
    call_state.pending_bxml = bxml
```

The `/callbacks/voice/continue/{callId}` route checks for pending BXML:

```python
@app.post("/callbacks/voice/continue/{call_id}")
async def voice_continue(call_id: str):
    call = event_store.get_call(call_id)
    if call and call.pending_bxml:
        bxml = call.pending_bxml
        call.pending_bxml = None
        return Response(content=bxml, media_type="application/xml")
    else:
        # Agent hasn't responded yet — redirect again (up to limit)
        return Response(
            content=f'<Response><Redirect redirectUrl="/callbacks/voice/continue/{call_id}" /></Response>',
            media_type="application/xml"
        )
```

---

## 8. Self-Configuring Callbacks

### Problem

Setting up webhook URLs requires manual work in the Bandwidth dashboard. The agent can't wire itself.

### Solution

The `configureCallbacks` tool updates a Bandwidth application's callback URLs to point at the hosted server's own address. The agent goes from "I need webhooks" to "webhooks are configured" in one tool call.

**Prerequisite:** `BW_MCP_BASE_URL` must be set (the server's public URL). In hosted mode this is auto-detected from the request. In dev mode with ngrok, set it manually.

**Safety:** The tool stores the previous callback URLs before overwriting, enabling restoration. A `restoreCallbacks` tool puts them back.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/app.py` | Modify | Add transport config, mount callback routes, pass instructions |
| `src/instructions.py` | Create | Dynamic instructions builder |
| `src/callbacks.py` | Create | Callback HTTP routes (Starlette) |
| `src/event_store.py` | Create | Event storage (ring buffer, per-call state) |
| `src/tools/callbacks.py` | Create | getInboundMessages, getCallbackEvents, configureCallbacks, restoreCallbacks |
| `src/tools/voice.py` | Create | generateBXML, respondToCallback, setVoiceHandler |
| `src/config.py` | Modify | Add transport, profile, base URL, fallback number config |
| `src/server_utils.py` | Modify | Add spec caching |
| `src/servers.py` | Modify | Add voice API spec |
| `src/resources.py` | No change | Instructions field replaces the need for proactive resource reading |
| `AGENTS.md` | Modify | Update to reflect new capabilities, stays as deep reference |
| `test/test_instructions.py` | Create | Instructions builder tests |
| `test/test_callbacks.py` | Create | Callback route tests |
| `test/test_event_store.py` | Create | Event store tests |
| `test/test_voice.py` | Create | BXML generation, voice handler tests |
| `pyproject.toml` | Modify | No new deps for MVP (Starlette is already under FastMCP) |

---

## Decisions (from design review)

| Question | Decision | Reasoning |
|----------|----------|-----------|
| Conversation summarization | **No.** Store raw events, LLM manages its own context. | The server is a buffer, not an editor. The LLM client is better at deciding what's relevant. |
| Event store persistence | **In-memory.** | Simple, no dependencies, sufficient for MVP. Events are ephemeral by nature. |
| Multi-tenant hosting | **Single-tenant.** One server per Bandwidth account. | No realistic scenario where multiple accounts share a server. Adds complexity for zero value. |
| Relay vs callback server | **Callback server only.** Defer relay. | Hosted HTTP server can receive callbacks directly. Relay only matters for stdio/local setups — build it if/when that need arises. |
| SSML support | **Light by default.** Auto-add pauses between sentences. Full SSML available via explicit verb fields. | Good default UX without requiring the agent to know SSML. |

## Open Questions

1. **Redirect chain limit:** Proposed 3 max (~2-3 seconds of buffer via redirect round-trips). When a voice gather callback arrives in agent-in-the-loop mode, the server responds with `<Redirect>` to buy time while the agent generates BXML. Each redirect is a network round-trip (~500ms-1s). After 3 redirects, fall back to a safe template. Should this be configurable per-deployment?

2. **Bandwidth application API access:** Does `configureCallbacks` need a different API endpoint or credential scope to update application callback URLs? Need to verify the Bandwidth Dashboard/Applications API supports this via the same Basic Auth credentials.

---

## Phasing

### Phase 1a: Instructions + Spec Cache (1-2 days)
- `src/instructions.py` with dynamic builder
- Instructions wired into `FastMCP()` constructor
- Spec caching in `server_utils.py`
- Tool profiles in `config.py`
- Tests

### Phase 1b: HTTP Transport + Callback Routes (2-3 days)
- Transport config (stdio/sse/streamable-http)
- MCP endpoint auth (bearer token)
- Callback HTTP routes mounted on Starlette
- Event store (in-memory ring buffer)
- `getInboundMessages`, `getCallbackEvents`, `configureCallbacks` tools
- Tests

### Phase 1c: Programmable Voice (3-4 days)
- `generateBXML` tool with all verb types
- Voice callback routes (answer, gather, disconnect, continue)
- Per-call conversation buffer
- `respondToCallback` tool
- Redirect-chain pattern for agent-in-the-loop
- Template-based voice handler (`setVoiceHandler`)
- Graceful degradation (fallback transfer)
- Tests

### Phase 1d: Polish (1-2 days)
- Update AGENTS.md with new capabilities
- Update README with hosting guide
- End-to-end integration test (send SMS → receive reply → respond)
- End-to-end voice test (inbound call → greeting → gather → respond → hangup)
