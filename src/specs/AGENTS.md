# Bandwidth MCP Server — Agent Reference

Structured reference for AI agents using the Bandwidth MCP Server. Covers the
tool inventory, the credentials each tool needs, the order to call things, and
how the server reports failure. Self-contained — an agent should not need to
cross-reference anything else to operate.

## Overview

The MCP server exposes a curated subset of Bandwidth's APIs as MCP tools.
Tools are grouped into workflow-oriented profiles (voice, messaging, lookup,
mfa, onboarding, recordings). Selecting a profile at startup limits the tools
loaded so the agent's context stays small. The surface complements the `band`
CLI — see [Limitations](#limitations) for what's not exposed here.

What the server does:
- One-shot API calls (create a message, place a call, run a lookup).
- State queries (get call state, list messages, fetch callback events).
- BXML generation and first-write-wins callback responses for live calls.
- Express Registration (account creation without prior credentials).

What the server does not do:
- Mid-call streaming or media manipulation. Voice is callback-driven through
  `respondToCallback`; the server is not a media server.
- Batch operations. Each tool acts on one resource.
- Message-body retrieval. Bandwidth does not store message contents.

## Auth

The server uses OAuth2 client credentials. Two ways to authenticate:

1. Set env vars before starting the server:
   - `BW_CLIENT_ID` — OAuth2 client ID
   - `BW_CLIENT_SECRET` — OAuth2 client secret
2. Call `setCredentials(client_id, client_secret)` mid-session. This is the
   path stdio sessions use when they bootstrap through Express Registration —
   `createRegistration` → `sendVerificationCode` → `verifyRegistrationCode`
   returns a fresh client_id/secret pair, which the agent then loads via
   `setCredentials` to unlock authenticated tools.

The account ID is discovered from the JWT `sub`/`accounts` claim after the
client_credentials grant. Agents never need to provide an account ID
manually.

`clearCredentials` logs the session out and forces re-auth on the next
authenticated call.

### Host URLs

Production is the default. `BW_ENVIRONMENT=test` (or `uat`) flips the API and
Voice hosts to the test environment in one shot, matching the CLI. Individual
hosts can also be overridden with their own env var; per-host overrides win
over `BW_ENVIRONMENT`.

| Env var | Purpose |
|---|---|
| `BW_ENVIRONMENT` | `test` / `uat` to target the test environment; unset for prod |
| `BW_API_URL` | API gateway base — also serves the Dashboard XML API under `/api/v2` |
| `BW_VOICE_URL` | Voice API base |
| `BW_MESSAGING_URL` | Messaging API base |

Leave them unset for normal use.

## Account types and capabilities

Two account shapes matter:

- **Bandwidth Build account.** Voice-only, credit-based. Messaging, number
  ordering/lookup-by-account, MFA over SMS, toll-free verification, and 10DLC
  are not available. A Build account ships with one pre-provisioned voice
  application and one phone number — the agent does not create either.
- **Full account.** Messaging, voice, lookup, MFA, and numbers all available
  subject to the credential's roles.

When a tool is invoked against an account that doesn't have the required
feature, the server returns the standard error shape (see [Output shape](#output-shape))
with `code: "feature_limit"` and a `recovery` hint pointing at the upgrade
path. The agent should treat `feature_limit` as non-retryable and surface the
hint to the user.

Two early calls let an agent branch correctly before doing real work:

1. `listApplications` — returns the apps already on the account. On a Build
   account this is the pre-provisioned voice app.
2. `listPhoneNumbers` — returns the numbers already on the account. On a Build
   account this is the pre-provisioned number.

If both return data, the agent can place a call without provisioning anything.

## Tool inventory

Tools are grouped by workflow. The grouping mirrors `src/profiles.py`. Loading
a single profile keeps unused tools out of the agent's context.

Always loaded:

| Tool | Purpose | Auth |
|---|---|---|
| `setCredentials` | Authenticate the session (client_id/secret) | none |
| `clearCredentials` | Log out the session | session token |

### Profile: `onboarding`

No credentials needed — use this to create an account from zero.

| Tool | Purpose | Check after |
|---|---|---|
| `createRegistration` | Start Express Registration with contact details | response carries a registration ID |
| `sendVerificationCode` | Trigger SMS OTP to the registered number | wait for the SMS to arrive at the user |
| `verifyRegistrationCode` | Confirm the OTP and finish registration | response carries `client_id` + `client_secret` — pass to `setCredentials` |

### Profile: `voice`

Auth: client_credentials. Voice application ID is required for `createCall`
(discover via `listApplications`).

| Tool | Purpose | Check after |
|---|---|---|
| `listApplications` | Find or confirm the voice app on the account | non-empty list, app type is voice |
| `createApplication` | Create a voice application with callback URLs | record the new `applicationId` |
| `listPhoneNumbers` | Find numbers usable as the `from` of a call | non-empty list |
| `createCall` | Initiate an outbound call | **always** poll `getCallState` — see [Trust nothing](#trust-nothing) |
| `getCallState` | Read the current state of a call | inspect `state` and `disconnectCause` |
| `listCalls` | List call events with filtering | — |
| `updateCall` | Redirect, hang up, or pause an active call | poll `getCallState` |
| `updateCallBxml` | Replace the BXML on an active call | poll `getCallState` |
| `generateBXML` | Build valid BXML from a verb list | inspect returned XML before sending |
| `respondToCallback` | Queue a BXML response for an active callback | first-write-wins; second writer gets `code: "conflict"` |
| `getCallbackEvents` | Read recent voice/messaging callback events | check `event_type` and `timestamp` |
| `configureCallbacks` | Point an application's callback URLs at this server | confirm via `listApplications` |

### Profile: `recordings`

Auth: client_credentials.

| Tool | Purpose | Check after |
|---|---|---|
| `listCallRecordings` | List recordings for a call | non-empty list |
| `getCallRecording` | Fetch metadata for one recording | `status` is `complete` |
| `deleteRecording` | Remove a recording | absent on next list |
| `downloadCallRecording` | Download the media | binary payload |
| `transcribeCallRecording` | Request transcription | poll `getRecordingTranscription` |
| `getRecordingTranscription` | Read transcription state | `status` is `complete` |

### Profile: `messaging`

Auth: client_credentials. Full account only — Build returns `feature_limit`.

| Tool | Purpose | Check after |
|---|---|---|
| `createMessage` | Send SMS or MMS | 202 means **accepted, not delivered**; watch `getCallbackEvents` for `message-delivered` / `message-failed` |
| `listMessages` | Query message history | requires at least one filter; timestamps must be millisecond precision (`2024-01-01T00:00:00.000Z`) |
| `getInboundMessages` | Read inbound SMS/MMS captured by this server | filter by number and time |
| `listMedia` / `getMedia` / `uploadMedia` / `deleteMedia` | Manage MMS media | URL from `uploadMedia` feeds `createMessage` |
| `configureCallbacks` | Point an application's callbacks at this server | confirm via `listApplications` |

### Profile: `mfa`

Auth: client_credentials.

| Tool | Purpose | Check after |
|---|---|---|
| `generateMessagingCode` | Send MFA code over SMS (full account) | response carries scope/issue time |
| `generateVoiceCode` | Send MFA code over voice (Build OK) | response carries scope/issue time |
| `verifyCode` | Validate a code the user entered | inspect `valid` boolean |

### Profile: `lookup`

Auth: client_credentials.

| Tool | Purpose | Check after |
|---|---|---|
| `createSyncLookup` | One-shot lookup (small input) | response is the result |
| `createAsyncBulkLookup` | Lookup for many numbers | poll `getAsyncBulkLookup` |
| `getAsyncBulkLookup` | Poll a bulk lookup | `status` is `complete` |

## Output shape

All tools return JSON dicts. Success responses are the tool's natural payload
— not wrapped in `{data: ...}`. The agent reads fields directly off the
response.

Failure responses use a single structured shape:

```json
{
  "error": "human-readable message",
  "code": "feature_limit | auth | not_found | rate_limited | conflict | timeout",
  "recovery": "what to try next"
}
```

Code semantics:

| Code | Meaning | Retryable? |
|---|---|---|
| `auth` | Credentials missing, expired, or invalid (401) | Re-auth via `setCredentials`, then retry |
| `feature_limit` | Account/credential cannot use this feature (402, 403 role/plan, Build limits) | No — surface `recovery` and stop |
| `not_found` | Resource ID does not exist (404) | No — verify the ID |
| `conflict` | Duplicate or first-write-wins loss (409, also `respondToCallback`) | Sometimes — query state first |
| `rate_limited` | Throttled or quota exceeded (429) | Yes, with backoff |
| `timeout` | Polling deadline exceeded with no terminal state | Query state and decide |

Agents should branch on `code`, not on `error` text. The text is for humans.

## Trust nothing

The most important rule for agents using this server: **`createCall` returns
immediately with a `callId` even when the call never actually goes out.** A
mis-provisioned `from` number, a routing failure, or a downstream carrier
reject all produce a happy 200/201 response with a valid-looking `callId`.

Always poll `getCallState` before reporting success to the user.

What to look at:

| Field | Healthy value | Bad value |
|---|---|---|
| `state` | `active`, then `completed` | stuck on `initiated` for more than a few seconds |
| `disconnectCause` | `hangup`, `busy`, `timeout` | `error` |
| `errorMessage` | absent | anything — especially `Service unavailable` |

If `disconnectCause` is `error`, the call never connected. Try a different
`from` number, or re-check provisioning via `listApplications` /
`listPhoneNumbers`.

The same rule applies to `createMessage`: a 202 means "accepted for
processing," not "delivered." Delivery confirmation arrives later through
`getCallbackEvents` as a `message-delivered` or `message-failed` event. Never
tell the user a message was delivered based solely on the `createMessage`
return value.

## Async operations

Several tools are async by design. The server does not block — the agent
polls.

| Tool | Poll with | Recommended interval | Notes |
|---|---|---|---|
| `createCall` | `getCallState` | 500ms–1s for the first few polls; 2–5s after | Call can fail silently; see [Trust nothing](#trust-nothing) |
| `createMessage` | `getCallbackEvents` filtered by `messageId` | 1–2s | Delivery only confirms via webhook |
| `transcribeCallRecording` | `getRecordingTranscription` | 5s | Transcription can take longer than the recording |
| `createAsyncBulkLookup` | `getAsyncBulkLookup` | 2–5s | Result includes per-number status |

`respondToCallback` has first-write-wins semantics: if two BXML responses race
for the same callback, the second returns `code: "conflict"` and is dropped.
This is intentional — it lets multiple agent sessions safely observe the same
call without stepping on each other. The agent that wants to drive the call
should be the first to write, and should treat `conflict` as "another writer
already responded; re-read `getCallbackEvents` for the next prompt."

The EventStore (the in-memory queue feeding `getCallbackEvents` and
`getInboundMessages`) holds events for a bounded TTL — assume on the order of
an hour. Don't rely on it as durable storage; pull events as soon as you need
them and persist anything you care about long-term.

## Provisioning workflows

### Place an outbound call (Build account)

A Build account ships with everything needed. The agent does not provision.

```
listApplications              # find the pre-provisioned voice app → applicationId
listPhoneNumbers              # find the pre-provisioned number → from
createCall(from, to,          # initiate
           applicationId,
           answerUrl)         # → callId
getCallState(callId)          # poll until state=completed
                              # verify disconnectCause != "error"
```

If `listPhoneNumbers` returns empty, the account is in a state the agent
cannot recover — escalate to the user.

### Send a message (full account)

```
listApplications              # find the messaging application → applicationId
listPhoneNumbers              # find the from number
createMessage(from, to,       # send
              applicationId,
              text)           # → 202 with messageId
getCallbackEvents(            # poll for delivery
  event_type="message-delivered" or
  event_type="message-failed",
  message_id=messageId)
```

If `createMessage` returns `code: "feature_limit"`, the account is Build —
surface the `recovery` hint and stop.

## Limitations

- **No batch operations.** Each tool acts on a single resource. Bulk lookup is
  the only exception, and it's still one tool call returning one request ID.
- **No message-content retrieval.** Bandwidth does not store message bodies.
  After send, the text is gone. `listMessages` returns metadata only —
  timestamps, direction, segment counts.
- **No 10DLC tools.** The server does not expose campaign creation, brand
  registration, or number-to-campaign assignment. Use the `band` CLI
  (`band tendlc`) or the Bandwidth App for these flows.
- **No toll-free verification tools.** TFV status checks and submission are
  available via the `band` CLI (`band tfv`), not here.
- **No number ordering / provisioning.** Search, order, activation, and
  release of new numbers live in the `band` CLI (`band number`) and the
  Bandwidth App. The MCP server can list numbers already on the account.
- **No sub-accounts, sites, locations, or peer assignments.** Account
  topology management is CLI-only today.
- **Build accounts are voice-only.** Anything outside voice / MFA-over-voice /
  app discovery returns `code: "feature_limit"`.
- **No real-time media.** Voice is callback/BXML driven. The server cannot
  stream audio, inject media mid-stream, or act as a media relay.
- **EventStore is in-memory.** Callback events are not durable across server
  restarts. Persistent capture requires an external store.
- **`setCredentials` is session-scoped.** Credentials set via the tool do not
  survive a server restart. For persistence, set `BW_CLIENT_ID` /
  `BW_CLIENT_SECRET` before starting the server.

## Error patterns

Common API failures and the structured response the agent will see:

| Trigger | Code | Recovery |
|---|---|---|
| `setCredentials` never called and env vars unset | `auth` | Call `setCredentials` or restart with env vars |
| Bearer token expired mid-session | `auth` | Server attempts silent refresh; on failure surfaces `auth` — agent re-calls `setCredentials` |
| Build account calls `createMessage` | `feature_limit` | Stop; surface upgrade path from `recovery` |
| Credential lacks a role (Campaign Mgmt, TFV) on full account | `feature_limit` | Escalate to the user's account manager |
| Tool referenced an ID that doesn't exist | `not_found` | Verify ID; re-list parents |
| Duplicate `createApplication` with same name | `conflict` | Re-list and reuse the existing one |
| Second writer to `respondToCallback` | `conflict` | Re-read `getCallbackEvents`; another session is driving |
| 429 from upstream | `rate_limited` | Exponential backoff and retry |
| Async poll exceeded deadline | `timeout` | Query the resource directly before retrying the originating call |
| `listMessages` called with zero filters | `error` (validation, surfaced verbatim) | Add at least one of `to`, `from`, `messageId`, or a date range |
| `listMessages` called with second-precision date | `error` | Use millisecond precision: `2024-01-01T00:00:00.000Z` |

The agent should branch on `code`. Treat `feature_limit`, `not_found`, and
validation errors as non-retryable. Treat `auth`, `rate_limited`, and
`timeout` as retryable after the appropriate corrective step.
