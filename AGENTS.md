# Bandwidth MCP Server — Agent Reference

This is the structured reference for AI agents using the Bandwidth MCP Server. It covers what tools exist, what credentials they need, the order to call things, and what can go wrong.

---

## Overview

The Bandwidth MCP Server exposes Bandwidth's APIs as MCP tools. Tools are auto-generated from OpenAPI specs at startup — there are no hand-written tool implementations for the core APIs. The server fetches live specs from `dev.bandwidth.com/spec/` and registers each endpoint as a named tool.

**APIs covered:**
- Messaging (SMS, MMS, RBM/multi-channel)
- Multi-Factor Authentication
- Phone Number Lookup
- Insights (reporting and analytics)
- End-User Management (compliance, addresses, requirements packages)
- Express Registration (account creation — no auth required)

---

## Prerequisites

### Required for most operations

```sh
BW_USERNAME     # Bandwidth API username
BW_PASSWORD     # Bandwidth API password
BW_ACCOUNT_ID   # Bandwidth account ID
```

### Conditionally required

```sh
BW_NUMBER                   # E.164 phone number on your account (e.g. +19195551234)
                            # Required for: Messaging, MFA
BW_MESSAGING_APPLICATION_ID # Required for: createMessage, createMultiChannelMessage,
                            #               generateMessagingCode
BW_VOICE_APPLICATION_ID     # Required for: generateVoiceCode
```

### Tool filtering (optional)

```sh
BW_MCP_TOOLS         # Comma-separated list of tools to enable (all enabled if unset)
BW_MCP_EXCLUDE_TOOLS # Comma-separated list of tools to disable (takes priority over BW_MCP_TOOLS)
```

CLI flags `--tools` and `--exclude-tools` take priority over the env vars.

### No credentials needed

Express Registration tools (`createRegistration`, `sendVerificationCode`, `verifyRegistrationCode`) work without `BW_USERNAME`/`BW_PASSWORD`. Use them to create an account first, then call `setCredentials` to load authenticated tools mid-session.

---

## Tool Discovery

Tools are generated from OpenAPI specs at startup, so the exact parameter names and shapes come from Bandwidth's live API specs. To discover available tools:

1. Read `resource://config` to see what credentials/config are loaded.
2. Check the server startup output — it prints every registered tool name.
3. Consult the [Tools List in README.md](README.md#tools-list) for the current canonical list.
4. Use `BW_MCP_TOOLS` to limit tools to only what you need — this reduces context window pressure and speeds up agent responses.

Tool names match their OpenAPI `operationId` exactly. These are stable across restarts.

---

## Available API Groups

### Express Registration

No auth required. Use this to create a new Bandwidth account from scratch.

| Tool | Description |
|---|---|
| `createRegistration` | Register a new Bandwidth account |
| `sendVerificationCode` | Send SMS verification code to the number |
| `verifyRegistrationCode` | Confirm the SMS code and complete registration |

**Enable:** `BW_MCP_TOOLS=createRegistration,sendVerificationCode,verifyRegistrationCode`

---

### Credentials (built-in tool, not from OpenAPI)

| Tool | Description |
|---|---|
| `setCredentials` | Set username, password, and account_id mid-session to unlock authenticated tools |

Call this after completing Express Registration if credentials weren't set at startup.

---

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

---

### Messaging

Requires: `BW_ACCOUNT_ID`, `BW_MESSAGING_APPLICATION_ID`, `BW_NUMBER`

| Tool | Description |
|---|---|
| `listMessages` | List messages with filtering options |
| `createMessage` | Send SMS or MMS |
| `createMultiChannelMessage` | Send multi-channel messages (RBM, SMS, MMS) |

**Enable:** `BW_MCP_TOOLS=listMessages,createMessage,createMultiChannelMessage`

---

### Multi-Factor Authentication

Requires: `BW_ACCOUNT_ID`, `BW_NUMBER`, and either `BW_MESSAGING_APPLICATION_ID` or `BW_VOICE_APPLICATION_ID` depending on the channel.

| Tool | Description |
|---|---|
| `generateMessagingCode` | Send MFA code via SMS |
| `generateVoiceCode` | Send MFA code via voice call |
| `verifyCode` | Verify a previously sent code |

**Enable:** `BW_MCP_TOOLS=generateMessagingCode,generateVoiceCode,verifyCode`

---

### Phone Number Lookup

Requires: `BW_ACCOUNT_ID`

| Tool | Description |
|---|---|
| `createLookup` | Create a lookup request for one or more phone numbers |
| `getLookupStatus` | Poll for results of a lookup request |

Lookup is async — always call `createLookup` first, then poll `getLookupStatus` with the returned request ID until status is complete.

**Enable:** `BW_MCP_TOOLS=createLookup,getLookupStatus`

---

### Insights (Reporting & Analytics)

Requires: `BW_ACCOUNT_ID`

| Tool | Description |
|---|---|
| `getReportDefinitions` | List available report types |
| `getReports` | Get history of created reports |
| `createReport` | Create a new report instance |
| `getReportStatus` | Poll for report completion |
| `getReportFile` | Download the completed report file |

Report generation is async. Call `createReport`, poll `getReportStatus`, then fetch with `getReportFile`.

---

### Media Management

Requires: `BW_ACCOUNT_ID`

| Tool | Description |
|---|---|
| `listMedia` | List media files on the account |
| `getMedia` | Download a specific media file |
| `uploadMedia` | Upload a media file |
| `deleteMedia` | Delete a media file |

---

### Voice & Call Management

Requires: `BW_ACCOUNT_ID`

| Tool | Description |
|---|---|
| `listCalls` | List call events with filtering |
| `listCall` | Get details for a single call event |

---

### End-User Management

Requires: `BW_ACCOUNT_ID`

#### Address Management

| Tool | Description |
|---|---|
| `getAddressFields` | Get supported address fields by country |
| `validateAddress` | Validate an address (no other tools needed for this) |
| `listAddresses` | List all addresses on the account |
| `createAddress` | Create an address |
| `getAddress` | Get an address by ID |
| `updateAddress` | Update an address |
| `listCityInfo` | Search city info |

#### Compliance

| Tool | Description |
|---|---|
| `listDocumentTypes` | List accepted document types and metadata requirements |
| `listEndUserTypes` | List end user types and accepted metadata |
| `listEndUserActivationRequirements` | List activation requirements for end users |
| `getComplianceDocumentMetadata` | Get metadata for an uploaded document |
| `updateComplianceDocument` | Modify a document |
| `downloadComplianceDocuments` | Download a document by ID |
| `createComplianceDocument` | Upload a document with metadata |
| `listComplianceEndUsers` | List all end users on the account |
| `createComplianceEndUser` | Create an end user |
| `getComplianceEndUser` | Get an end user by ID |
| `updateComplianceEndUser` | Update an end user |

#### Requirements Packages

| Tool | Description |
|---|---|
| `listRequirementsPackages` | List all requirements packages |
| `createRequirementsPackage` | Create a requirements package |
| `getRequirementsPackage` | Get a requirements package |
| `patchRequirementsPackage` | Update a requirements package |
| `getRequirementsPackageAssets` | Get assets attached to a package |
| `attachRequirementsPackageAsset` | Attach an asset to a package |
| `detachRequirementsPackageAsset` | Detach an asset from a package |
| `validateNumberActivation` | Validate number activation requirements |
| `getRequirementsPackageHistory` | Get history of a requirements package |

---

## Common Workflows

### Send an SMS

Prerequisites: `BW_ACCOUNT_ID`, `BW_MESSAGING_APPLICATION_ID`, `BW_NUMBER`

1. Call `createMessage` with `to`, `from` (your `BW_NUMBER`), `applicationId` (`BW_MESSAGING_APPLICATION_ID`), and `text`.
2. Optionally call `listMessages` to confirm delivery status.

### Look Up a Phone Number

Prerequisites: `BW_ACCOUNT_ID`

1. Call `createLookup` with the target number(s).
2. Take the `requestId` from the response.
3. Call `getLookupStatus` with that `requestId`.
4. If status is not complete, poll again. Most lookups resolve quickly.

### Send and Verify an MFA Code

Prerequisites: `BW_ACCOUNT_ID`, `BW_NUMBER`, application ID for chosen channel

1. Call `generateMessagingCode` (SMS) or `generateVoiceCode` (voice call) with `to`, `from`, `applicationId`, `scope`, and `digits`.
2. User receives and enters the code.
3. Call `verifyCode` with `to`, `scope`, and the entered `code`. Returns whether the code is valid.

### Register a New Account (Express Registration)

No credentials needed at startup.

1. Call `createRegistration` with account details.
2. Call `sendVerificationCode` to send an SMS to the registered number.
3. Call `verifyRegistrationCode` with the received code.
4. Call `setCredentials` with the new `username`, `password`, and `account_id` to load authenticated tools.

### Add a Business End User

Prerequisites: `BW_ACCOUNT_ID`

1. Call `listEndUserTypes` to see available types and their required fields.
2. Optionally call `listEndUserActivationRequirements` if the end user will be tied to requirements packages.
3. Call `createComplianceEndUser` with the required fields for your chosen type.

### Validate an Address

Prerequisites: `BW_ACCOUNT_ID`

1. Call `validateAddress` directly. No setup steps needed.

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

---

## Resources

These are MCP resources (not tools) — they return static or config data.

| URI | Name | Description |
|---|---|---|
| `resource://config` | Bandwidth API Configuration | JSON object with loaded credentials, application IDs, and account ID. Check this first to confirm what's configured. |
| `resource://number_order_guide` | Bandwidth Number Order Guide | Markdown guide for searching and ordering phone numbers. |
| `resource://mcp_agent_reference` | Bandwidth MCP Agent Reference | This document — the full agent reference for the MCP server. |

Read `resource://config` at the start of a session to confirm which environment variables are set before calling authenticated tools.

---

## Error Patterns

**`BW_USERNAME and BW_PASSWORD required for authenticated APIs`**
Credentials weren't set at startup and `setCredentials` hasn't been called. Either set the env vars before starting the server, or use the Express Registration flow followed by `setCredentials`.

**`Warning: Failed to create server for {api_name}`**
The OpenAPI spec fetch failed at startup (network issue, spec URL down). The affected API group's tools won't be available. Restart the server when connectivity is restored.

**401 Unauthorized from API calls**
Credentials are wrong or expired. Double-check `BW_USERNAME` and `BW_PASSWORD`.

**422 / validation errors from API calls**
Required fields are missing or formatted wrong. Check the parameter shapes — phone numbers must be in E.164 format (e.g. `+19195551234`). Application IDs are UUIDs.

**Tool not found / tool not registered**
Either `BW_MCP_TOOLS` is set and doesn't include the tool you need, or `BW_MCP_EXCLUDE_TOOLS` is excluding it. Check the filter config.

**Context window / slow responses**
All tools are enabled. Use `BW_MCP_TOOLS` to enable only the subset you need.

**Async operations returning "pending"**
Phone number lookup and report generation are async. Poll the status tool (`getLookupStatus`, `getReportStatus`) until the result is ready — don't treat a pending response as a failure.

---

## Limitations

- **No Voice API tools** — call management (`listCalls`, `listCall`) is read-only; there are no tools to initiate or control live calls.
- **No number ordering** — the server can look up number availability (via the number order guide resource) but doesn't have tools to purchase or provision numbers directly.
- **Tools are read from live specs** — if Bandwidth's spec URLs are unreachable at startup, those API groups won't load. There's no local fallback.
- **Tool filtering is all-or-nothing per name** — you can't partially expose a tool (e.g. read-only vs. write). Enable or exclude whole tools by name.
- **No webhook registration** — the server makes outbound API calls but doesn't receive inbound callbacks or set up webhooks.
- **`setCredentials` is session-scoped** — credentials set via the tool don't persist across server restarts. Set env vars for persistence.
- **Claude Desktop resource limitation** — Claude Desktop has known issues reading MCP resources. If `resource://config` isn't accessible, pass credential-dependent parameters (account ID, application ID, phone number) manually in your prompts.
