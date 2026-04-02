# Bandwidth Agent Platform — Design Spec

**Date:** 2026-03-27
**Author:** Kush Shah + Claude
**Status:** Draft

## BLUF

Bandwidth wants its APIs to be agent-native — an AI agent should be able to go from a user prompt ("Call +15555555555 and talk to them about X") to a working phone call, with zero human intervention. The existing MCP server is the right foundation. Expanding it to cover the full agent flow (account creation → infrastructure setup → number provisioning → call management → callback handling) is the critical path. A CLI complements this for human developer experience but is not required for the agent story.

## Goal

An agent can autonomously:

1. Create a Bandwidth account (Express Registration)
2. Set up infrastructure (site, location, voice application)
3. Purchase a phone number
4. Configure callback handling
5. Make and receive voice calls
6. Respond to callbacks with BXML to control call flow
7. Access recordings and transcriptions after calls

All through Bandwidth's MCP server, with no human intervention beyond the initial prompt.

## Architecture

### System Components

```
┌─────────────────────────────────────────────────┐
│                   AI Agent                       │
│  (Claude, GPT, Gemini, custom)                  │
│                                                  │
│  Discovers tools via MCP protocol                │
└──────────────┬──────────────────────────────────┘
               │ MCP protocol
               ▼
┌─────────────────────────────────────────────────┐
│           Bandwidth MCP Server                   │
│  (existing: Bandwidth/mcp-server, Python)        │
│                                                  │
│  ┌─────────┐ ┌─────────┐ ┌──────────┐          │
│  │ Express │ │ Account │ │  Voice   │          │
│  │  Reg    │ │  Infra  │ │  Calls   │          │
│  └────┬────┘ └────┬────┘ └────┬─────┘          │
│       │           │           │                  │
│  ┌────┴────┐ ┌────┴────┐ ┌───┴──────┐          │
│  │ Numbers │ │  BXML   │ │ Listener │          │
│  │         │ │  Gen    │ │ (relay)  │          │
│  └────┬────┘ └─────────┘ └────┬─────┘          │
│       │                       │                  │
└───────┼───────────────────────┼──────────────────┘
        │ HTTPS                 │ WSS
        ▼                       ▼
┌──────────────┐    ┌────────────────────┐
│  Bandwidth   │    │   Relay Service    │
│  APIs        │    │   (new, Go)        │
│              │    │                    │
│  - Express   │    │  Swaps callback    │
│  - Iris/VPS  │    │  URLs in VPS.      │
│  - Voice     │    │  Proxies events    │
│  - Numbers   │    │  to MCP server     │
│              │    │  via WebSocket.    │
└──────────────┘    └────────┬───────────┘
                             │ internal
                             ▼
                    ┌─────────────────┐
                    │ Callback Proxy  │
                    │ (existing,      │
                    │  http-voice-v2) │
                    │                 │
                    │ Central voice   │
                    │ callback        │
                    │ dispatcher.     │
                    │                 │
                    │ POSTs to relay  │
                    │ URL (thinks     │
                    │ it's a normal   │
                    │ customer URL).  │
                    └─────────────────┘
```

### Component Responsibilities

**Bandwidth MCP Server** (existing, expand)
- Primary agent interface for all Bandwidth operations
- Generates tools from OpenAPI specs (existing architecture)
- New: Express registration tools (from spec)
- New: Account infrastructure tools (sites, locations, apps)
- New: `listen` capability (WebSocket to relay, forward callbacks locally)
- New: BXML generation tools
- New: Quickstart orchestration tool (composed multi-step setup)

**Relay Service** (new, build)
- Lightweight Go WebSocket server deployed to Bandwidth infra
- Accepts authenticated connections from MCP server's listen tool
- On connect: stores original callback URL, swaps app's callback URL in VPS to point at relay's HTTP endpoint
- On callback received: pushes event over WebSocket to MCP → MCP forwards to user's local server → BXML response flows back
- On disconnect: restores original callback URL in VPS
- Heartbeat/TTL cleanup for crashed sessions

**Callback Proxy** (existing, NO changes)
- Already the central dispatcher for all voice callbacks in http-voice-v2
- Two endpoints: `/callback/bxml` (sync, expects BXML back) and `/callback/async` (fire-and-forget)
- Relay Service works by swapping the callback URL, so Callback Proxy treats it as just another customer URL. Zero modifications needed.

## Decisions Made

| Decision | Choice | Why |
|----------|--------|-----|
| Primary agent interface | MCP server (existing) | Already works, generates tools from specs, agent-native |
| CLI | Deferred (nice-to-have) | Serves humans, not agents. Build when developer adoption story matters. |
| Relay approach | Option B: virtual callback URL swap via VPS | No changes to Callback Proxy or Call Engine. Fully self-contained. |
| Relay ↔ Callback Proxy | Transparent (relay IS the callback URL) | Zero changes to existing voice infrastructure |
| MCP server language | Python (existing) | Already built, FastMCP framework, OpenAPI spec generation |
| Relay service language | Go | Team has Go developers, single binary, good WebSocket support |
| Repo structure | Relay in its own repo, MCP expansion in existing repo | MCP server already exists at Bandwidth/mcp-server |
| Scope | Voice + numbers for v1, messaging already covered | Voice is the priority; messaging already works in MCP |
| Open source | Yes, both MCP server and eventual CLI | Developer adoption, community trust |
| Composite MCPs | Tool filtering via BW_MCP_TOOLS env var (existing) | Agents load only tools they need, keeps context small |

## The Agent Flow (End to End)

### Step 1: Account Creation (Express Registration)

**MCP tools needed:**
- `createRegistration(phoneNumber, email, firstName, lastName)` → registrationId
- `sendVerificationCode(phoneNumber, email)` → VERIFICATION_CODE_SENT
- `verifyCode(phoneNumber, email, code)` → PHONE_VERIFIED

**Source:** Express Registration API spec (`internal/express.yml` on `DREAM-2177-bw-express-registration` branch). Currently limited to @bandwidth.com emails.

**Note:** After verification, the agent receives account credentials (accountId, API username/password). These need to be stored securely by the MCP server for subsequent calls.

### Step 2: Infrastructure Setup

**MCP tools needed:**
- `createSite(name)` → siteId
- `createLocation(siteId, name, isDefault)` → locationId (SIP Peer)
- `createVoiceApplication(name, callInitiatedCallbackUrl, callStatusCallbackUrl)` → applicationId
- `configureLocationVoice(siteId, locationId, applicationId)` → links app to location

**Orchestration tool:**
- `quickstartSetup(callbackUrl, areaCode?)` → creates site, location, voice application, and optionally orders a number. Returns { siteId, locationId, applicationId, phoneNumber? }. Handles the full "zero to ready" setup in one call.

**Source:** Iris/VPS APIs. Account management specs in `api-specs/external/accounts.yml` and `api-specs/external/applications.yml`.

### Step 3: Number Provisioning

**MCP tools needed:**
- `searchAvailableNumbers(areaCode?, state?, city?, quantity)` → list of available TNs
- `orderNumbers(numbers[], siteId)` → orderId
- `getOrderStatus(orderId)` → status (poll until COMPLETE)
- `listNumbers()` → in-service numbers

**Source:** Numbers API specs in `api-specs/numbers_spec/` and `api-specs/external/numbers.yml`.

### Step 4: Callback Handling

**MCP tools needed:**
- `startListening(applicationId, forwardTo)` → opens WebSocket to relay, starts forwarding callbacks to forwardTo URL
- `stopListening()` → closes connection, restores original callback URL

**Relay service flow:**
1. MCP calls relay service's registration endpoint with applicationId + BW credentials
2. Relay stores original callback URL from VPS
3. Relay updates app's callback URL in VPS to `https://relay.bandwidth.com/callback/{sessionId}`
4. Callback Proxy (unchanged) POSTs callbacks to relay's URL
5. Relay pushes events over WebSocket to MCP server
6. MCP forwards to user's local server (e.g., localhost:3000)
7. User's server responds with BXML
8. BXML flows back: local server → HTTP response → MCP receives over WS → sends back over WS → relay returns BXML as HTTP response to Callback Proxy → Callback Proxy converts to BJSON → Call Engine executes

### Step 5: Making and Receiving Calls

**MCP tools needed:**
- `createCall(from, to, applicationId, answerUrl)` → callId
- `listCalls(from?, to?, status?)` → call list
- `getCall(callId)` → call state
- `updateCall(callId, state?, redirectUrl?)` → redirect or hangup
- `updateCallBxml(callId, bxml)` → replace active BXML

**Source:** Voice API spec at `api-specs/external/voice.yml`.

### Step 6: BXML Generation

**MCP tools needed:**
- `generateBxml(verbs[])` → valid BXML string
  - Each verb is described as a structured object: `{ type: "SpeakSentence", text: "Hello", voice: "julie" }`
  - Supports: SpeakSentence, Gather, Transfer, Bridge, Conference, Record, PlayAudio, Forward, Pause, Ring, Redirect, Hangup, StartRecording, StopRecording, StartStream, StopStream, StartTranscription, StopTranscription, SendDtmf, Tag
- `validateBxml(bxml)` → validation result

**Note:** This is pure local logic in the MCP server, no API call. The MCP server constructs valid XML from structured input. This prevents agents from generating malformed BXML.

### Step 7: Post-Call Operations

**MCP tools needed:**
- `listRecordings(callId)` → recordings
- `getRecording(callId, recordingId)` → recording metadata
- `downloadRecording(callId, recordingId)` → audio file
- `createTranscription(callId, recordingId)` → async transcription request
- `getTranscription(callId, recordingId)` → transcription text

**Source:** Voice API spec, recordings and transcriptions endpoints.

## Relay Service Design

### Architecture

```
┌──────────────────────────────────────┐
│          Relay Service (Go)          │
│                                      │
│  ┌────────────┐  ┌───────────────┐  │
│  │ HTTP API   │  │ WebSocket     │  │
│  │            │  │ Manager       │  │
│  │ POST       │  │               │  │
│  │ /callback/ │  │ Authenticated │  │
│  │ {sessionId}│  │ WS conns from │  │
│  │            │  │ MCP clients   │  │
│  │ Receives   │  │               │  │
│  │ callbacks  │  │ Pushes events │  │
│  │ from       │  │ to matched    │  │
│  │ Callback   │  │ sessions      │  │
│  │ Proxy      │  │               │  │
│  └─────┬──────┘  └───────┬───────┘  │
│        │                 │           │
│  ┌─────┴─────────────────┴────────┐  │
│  │       Session Registry         │  │
│  │                                │  │
│  │  sessionId → {                 │  │
│  │    appId,                      │  │
│  │    wsConn,                     │  │
│  │    originalCallbackUrl,        │  │
│  │    createdAt,                  │  │
│  │    lastHeartbeat               │  │
│  │  }                             │  │
│  └────────────────────────────────┘  │
│                                      │
│  ┌────────────────────────────────┐  │
│  │       VPS Client               │  │
│  │                                │  │
│  │  On connect: read original     │  │
│  │  URL, swap to relay URL        │  │
│  │                                │  │
│  │  On disconnect: restore        │  │
│  │  original URL                  │  │
│  │                                │  │
│  │  TTL cleanup: restore URLs     │  │
│  │  for dead sessions             │  │
│  └────────────────────────────────┘  │
└──────────────────────────────────────┘
```

### Session Lifecycle

1. **Connect:** MCP server calls relay's `/sessions` endpoint with appId + credentials. Relay validates credentials against Bandwidth auth, reads current callback URL from VPS, stores it, swaps callback URL to `https://relay.bandwidth.com/callback/{sessionId}`. Returns sessionId + WebSocket URL.

2. **Listen:** MCP opens WebSocket to `wss://relay.bandwidth.com/ws/{sessionId}`. Relay authenticates the WS handshake. Events from Callback Proxy arrive at the HTTP endpoint and are pushed over the WS.

3. **BXML flow:** For `/callback/bxml` events (synchronous callbacks expecting BXML), the relay pushes the event over WS, waits for the BXML response from the MCP (with a timeout), and returns it as the HTTP response to Callback Proxy.

4. **Disconnect:** MCP closes WebSocket (or calls `/sessions/{sessionId}` DELETE). Relay restores original callback URL in VPS.

5. **Crash recovery:** TTL-based cleanup job runs every 60s. Any session with no heartbeat for 5 minutes has its original URL restored and the session is removed.

### Security

- All connections authenticated with Bandwidth API credentials
- Relay validates that the authenticated user owns the application they're subscribing to
- HTTPS/WSS only
- Relay never stores or logs BXML content (customer business logic)
- Session IDs are UUIDs, not guessable
- Rate limiting on session creation

## MCP Server Expansion Plan

### New API Specs to Add

| Spec | Source | Tools Generated |
|------|--------|-----------------|
| Express Registration | `internal/express.yml` (DREAM-2177 branch) | ~3 tools |
| Account Management | `external/accounts.yml` | ~10 tools (sites, locations CRUD) |
| Applications | `external/applications.yml` | ~6 tools (voice/messaging app CRUD) |
| Numbers (v2) | `external/numbers_v2.yml` or `numbers_spec/v2/` | ~8 tools (search, order, manage) |
| Voice (expanded) | `external/voice.yml` | ~15 tools (calls, conferences, recordings, transcriptions) |

### New Custom Tools (not spec-generated)

| Tool | Type | Description |
|------|------|-------------|
| `quickstartSetup` | Orchestration | Composed flow: create site → location → app → order number |
| `startListening` | Stateful | Opens WebSocket to relay, begins callback forwarding |
| `stopListening` | Stateful | Closes relay connection, restores callback URL |
| `generateBxml` | Local logic | Constructs valid BXML from structured verb descriptions |
| `validateBxml` | Local logic | Validates BXML string for correctness |

### Tool Filtering

The existing `BW_MCP_TOOLS` / `BW_MCP_EXCLUDE_TOOLS` mechanism handles context size. Recommended agent configurations:

- **Full voice flow:** `BW_MCP_TOOLS=createRegistration,sendVerificationCode,verifyCode,quickstartSetup,searchAvailableNumbers,orderNumbers,startListening,createCall,generateBxml`
- **Messaging only:** `BW_MCP_TOOLS=createMessage,listMessages` (already works)
- **Account management:** `BW_MCP_TOOLS=createRegistration,sendVerificationCode,verifyCode,createSite,createLocation,createVoiceApplication`

## CLI (Deferred — Phase 2)

The CLI serves humans, not agents. Build when the developer adoption/debugging/CI story becomes a priority.

### Value Proposition

1. **Developer adoption funnel** — first touch for developers exploring Bandwidth
2. **Debugging agent behavior** — inspect state after agent operations
3. **CI/CD automation** — provision test environments, run smoke tests
4. **Open source marketing** — `bw` as a brand presence in the developer ecosystem

### Architecture (when built)

- **Language:** Go + Cobra
- **Approach:** Curated commands first, spec-driven generation later (Approach C)
- **Repo:** Standalone repo (`Bandwidth/bw-cli`)
- **Scope:** Full API surface (~26 commands) + `listen`, `bxml`, `auth`, `quickstart`
- **Relationship to MCP:** Parallel interface, not a dependency. Both call Bandwidth APIs directly.

## Phasing

### Phase 0: Express Registration (target: March 30, 2026)

- Add Express Registration spec to MCP server
- 3 new tools: createRegistration, sendVerificationCode, verifyCode
- Ship alongside Express API launch

### Phase 1: Full Agent Voice Flow (target: ~4-5 weeks after Phase 0)

- Add account management, numbers, expanded voice specs to MCP server
- Build `quickstartSetup` orchestration tool
- Build `generateBxml` and `validateBxml` tools
- Build relay service (Go)
- Add `startListening` / `stopListening` tools to MCP
- End-to-end demo: agent goes from zero to phone call

### Phase 2: CLI + Polish (target: TBD)

- Build Go CLI with full API surface
- `bw listen` (uses same relay service)
- `bw quickstart` (interactive guided flow)
- Open source launch
- Developer documentation and guides

### Phase 3: Scale (target: TBD)

- Spec-driven CLI command generation from OpenAPI
- Messaging expansion in MCP (beyond current coverage)
- WebRTC support
- Callback templates repo for common patterns
- Agent-to-agent voice communication (the Innovation Studio vision)

## Open Questions

1. **Express email restriction:** Currently limited to @bandwidth.com. When does this open to public emails? This gates the "agent creates an account autonomously" story for external users.

2. **Relay service hosting:** Where does it deploy? Existing Bandwidth AWS infrastructure? Separate service? What team owns it?

3. **VPS API access:** Does the relay service need special permissions to read/write application callback URLs in VPS? Or can it use the same credentials as the customer?

4. **Callback URL restoration reliability:** If the relay crashes, the TTL cleanup restores URLs. But what's the acceptable window where a customer's callbacks go to a dead relay URL? Need to define the SLA.

5. **Multiple listen sessions per app:** Can two developers listen to the same app simultaneously? If yes, the relay needs to fan out events to multiple WebSocket connections. If no, need to handle the "session already exists" case.

6. **BXML response timeout:** When the relay forwards a synchronous BXML callback over WebSocket and waits for a response, what's the timeout? Callback Proxy already has a callbackTimeout (1-25s, configured per app). The relay's internal timeout should be shorter than that to return an error response rather than letting Callback Proxy time out.

7. **Rate limiting:** Should the relay limit how many sessions a single account can have? How many events per second per session?
