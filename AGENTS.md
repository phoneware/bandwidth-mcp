# Bandwidth CLI — Agent Reference

> Machine-readable reference for AI agents using the `bw` CLI.
> This file describes what each command does, when to use it, what it returns, and how commands relate to each other.

## Prerequisites

Before using any API command, the user must be authenticated:

```
bw auth login
```

This stores credentials in the OS keychain and saves the account ID to `~/.bw/config.json`. All API commands will fail with "not logged in" until this is done.

## Architecture

Bandwidth's voice platform requires infrastructure to be set up before you can make calls. The dependency chain is:

```
auth login
  └─→ site create
        └─→ location create (requires --site)
              └─→ app create (voice application with callback URL)
                    └─→ number search → number order (assigns to site)
                          └─→ call create (requires --from number, --app-id, --answer-url)
```

**`bw quickstart`** collapses this entire chain into one command. Use it when starting from scratch. Use individual commands when you need fine-grained control or already have partial infrastructure.

## Output

All commands output JSON by default. Use `--format table` for human-readable output. When scripting or when an agent is consuming output, always use the default JSON format.

Errors are printed to stderr and return a non-zero exit code. The error message includes guidance on how to fix the issue (e.g., "run `bw auth login` first").

## Global Flags

| Flag | Purpose |
|------|---------|
| `--format <json\|table>` | Output format (default: json) |
| `--account-id <id>` | Override the stored account ID for this command |

## Command Reference

### Authentication

Commands that manage local credentials. No API calls except implicit validation.

#### `bw auth login`
Interactive. Prompts for username (email), password, and account ID. Stores password in OS keychain, saves username and account ID to config file. **Must be run before any other API command.**

#### `bw auth status`
Prints current authentication state: who you're logged in as, what account ID is configured. Returns non-zero if not authenticated. Use this to check if auth is set up before attempting API operations.

#### `bw auth logout`
Clears stored credentials from keychain and config. Idempotent — safe to call even if not logged in.

---

### Account Registration

For creating new Bandwidth accounts. Most agents will skip this — the user typically already has an account.

#### `bw account register`
Creates a new Bandwidth account via Express Registration. Requires `--phone`, `--email`, `--first-name`, `--last-name`. Returns registration status. This is the first step of a three-step flow: register → send-code → verify.

#### `bw account send-code`
Sends or resends an SMS verification code. Requires `--phone`, `--email`. Use after `register` if the code wasn't received.

#### `bw account verify`
Completes phone verification with the SMS code. Requires `--phone`, `--email`, `--code`. After this succeeds, the account is active.

---

### Sites (Sub-Accounts)

Sites are the top-level organizational unit in Bandwidth's account hierarchy. You need at least one site before you can create locations or order numbers.

#### `bw site create`
Creates a site. Requires `--name`. Optional `--description`. Returns the created site object with its ID. **Save the site ID — you'll need it for location and number operations.**

#### `bw site list`
Returns all sites on the account. Use this to find existing site IDs.

#### `bw site get <site-id>`
Returns details for a specific site.

#### `bw site delete <site-id>`
Deletes a site. Will fail if the site has active locations or numbers. Remove those first.

---

### Locations (SIP Peers)

Locations are children of sites. They define where numbers are routed. You need a location before ordering numbers.

#### `bw location create`
Creates a location under a site. Requires `--site <site-id>` and `--name`. Returns the created location with its ID.

#### `bw location list`
Lists locations for a site. Requires `--site <site-id>`.

---

### Applications

Applications define how Bandwidth handles voice or messaging events — specifically, what callback URLs to use. You need a voice application before making calls.

#### `bw app create`
Creates an application. Requires `--name`, `--type <voice|messaging>`, and `--callback-url`. Returns the created application with its ID. **Save the application ID — you'll need it for calls.**

#### `bw app list`
Returns all applications on the account. Use this to find existing application IDs.

#### `bw app get <app-id>`
Returns details for a specific application.

#### `bw app delete <app-id>`
Deletes an application. Will fail if numbers are still assigned to it.

---

### Numbers

Phone numbers that you own and can use for making/receiving calls.

#### `bw number search`
Searches for available phone numbers to purchase. Requires `--area-code`. Optional `--quantity` (default 10). Returns a list of available numbers. **These are not yet owned — you must order them.**

#### `bw number order <number> [number...]`
Orders one or more phone numbers. Accepts E.164 format numbers as positional arguments (e.g., `+19195551234`). Numbers must come from a `number search` result. Returns order status.

#### `bw number list`
Lists all phone numbers currently owned by the account. Use this to find numbers available for making calls.

#### `bw number release <number>`
Releases (disconnects) a phone number. This is permanent — the number goes back to the pool.

---

### Calls

Voice call management. Requires a voice application and a phone number.

#### `bw call create`
Makes an outbound voice call. Requires:
- `--from <number>` — A number you own (from `number list`)
- `--to <number>` — Destination number in E.164 format
- `--app-id <id>` — Voice application ID (from `app create` or `app list`)
- `--answer-url <url>` — URL that Bandwidth will POST to for call instructions (must return BXML)

Returns call metadata including the call ID. **The call is asynchronous — it starts dialing immediately but the command returns before the call connects.**

#### `bw call list`
Lists recent calls on the account.

#### `bw call get <call-id>`
Returns current state of a specific call (active, completed, etc.) plus metadata.

#### `bw call hangup <call-id>`
Terminates an active call immediately.

#### `bw call update <call-id>`
Redirects an active call to a new URL. Requires `--redirect-url`. The new URL will be POSTed to for fresh BXML instructions.

---

### Recordings

Manage recordings of voice calls. Recordings are children of calls.

#### `bw recording list <call-id>`
Lists all recordings for a call.

#### `bw recording get <call-id> <recording-id>`
Returns metadata for a specific recording.

#### `bw recording download <call-id> <recording-id>`
Downloads the recording audio file. Requires `--output <filename>`. Writes binary audio data to the file.

#### `bw recording delete <call-id> <recording-id>`
Permanently deletes a recording.

---

### Transcriptions

Request and retrieve transcriptions of call recordings.

#### `bw transcription create <call-id> <recording-id>`
Requests a transcription for a recording. Transcription is asynchronous — poll with `transcription get` until complete.

#### `bw transcription get <call-id> <recording-id>`
Returns the transcription for a recording. May return a "pending" status if transcription is still processing.

---

### BXML Generation

Local-only commands that generate Bandwidth XML (BXML) for controlling calls. These do NOT make API calls — they output XML to stdout. Useful for generating static BXML to host at an answer URL, or for validating BXML syntax.

#### `bw bxml speak <text>`
Generates a SpeakSentence BXML response. Optional `--voice <name>` for TTS voice selection (e.g., julie, paul, bridget). Text is XML-escaped automatically.

#### `bw bxml gather`
Generates a Gather BXML response for collecting DTMF input. Requires `--url <callback-url>`. Optional `--max-digits`, `--prompt <text>`.

#### `bw bxml transfer <number>`
Generates a Transfer BXML response. Optional `--caller-id <number>`.

#### `bw bxml record`
Generates a Record BXML response. Optional `--url <callback-url>`, `--max-duration <seconds>`.

#### `bw bxml raw <xml-string>`
Validates an XML string for well-formedness. Prints the input if valid, returns an error if malformed. Does NOT validate BXML verb names — only checks XML syntax.

---

### Quickstart

#### `bw quickstart`
**One-command setup.** Creates a site, location, voice application, searches for a number, and orders it. Requires `--callback-url`. Optional `--area-code` (default 919), `--name` (default "Quickstart").

Returns a JSON object with all created resource IDs:
```json
{
  "siteId": "...",
  "locationId": "...",
  "applicationId": "...",
  "phoneNumber": "+19195551234",
  "status": "complete"
}
```

If no numbers are available in the area code, returns `"status": "complete_no_number"` with `phoneNumber: null`. The site, location, and app are still created.

**Use this instead of manually creating site → location → app → number when starting from scratch.**

---

## Common Workflows

### Make a call from scratch (no existing infrastructure)

```bash
bw auth login
bw quickstart --callback-url https://your-server.com/voice
# Returns: applicationId, phoneNumber
bw call create --from <phoneNumber> --to +15559876543 --app-id <applicationId> --answer-url https://your-server.com/voice
```

### Make a call with existing infrastructure

```bash
bw auth status          # Verify logged in
bw number list          # Find a number to call from
bw app list             # Find the application ID
bw call create --from <number> --to +15559876543 --app-id <appId> --answer-url <url>
```

### Check call result

```bash
bw call get <call-id>                          # Call state + metadata
bw recording list <call-id>                    # Any recordings
bw transcription create <call-id> <rec-id>     # Request transcription
bw transcription get <call-id> <rec-id>        # Get transcript text
```

### Generate BXML for a callback server

```bash
bw bxml speak "Hello, how can I help you?"
bw bxml gather --url https://server.com/gather --prompt "Press 1 for yes, 2 for no"
bw bxml transfer +15551234567
```

## Environment Variables

| Variable | Purpose | Overrides |
|----------|---------|-----------|
| `BW_ACCOUNT_ID` | Account ID | Config file value |
| `BW_USERNAME` | Username | Config file value |
| `BW_FORMAT` | Output format | Config file value (but not --format flag) |

## Error Patterns

| Error | Cause | Fix |
|-------|-------|-----|
| "not logged in" | No stored credentials | Run `bw auth login` |
| "account ID not set" | No account ID in config or flag | Run `bw auth login` or pass `--account-id` |
| "API error 401" | Invalid credentials | Re-run `bw auth login` with correct password |
| "API error 403" | No permission for this resource | Check account ID is correct |
| "API error 404" | Resource doesn't exist | Verify the ID is correct |
| "required flag not set" | Missing a required flag | Check `--help` for required flags |

## Limitations

- **No real-time call control.** The CLI can initiate calls and query their state, but cannot receive or respond to mid-call callbacks. Dynamic call control requires a server that handles Bandwidth's webhook callbacks and responds with BXML.
- **No streaming.** Call creation is fire-and-forget. Use `bw call get <id>` to poll for call state changes.
- **No batch operations.** Each command operates on one resource at a time.
