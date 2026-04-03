"""Dynamic MCP instructions builder.

Generates the instructions string sent to LLM clients during MCP initialization.
Adapts content based on which tools are actually loaded and what config is present.
"""

from typing import Any

HEADER = """# Bandwidth MCP Server

You have access to Bandwidth's communication APIs as MCP tools. Everything you need is available as tools on this server — do NOT read source code, make raw curl calls, or explore the codebase. Just use the tools.

## Quick Start
- Read resource://config to see what's configured (credentials, account ID, application IDs, phone numbers).
- Phone numbers must be in E.164 format (e.g. +19195551234).
- Application IDs are UUIDs.
- All tools are listed in your tool list. If you need to discover account resources (applications, phone numbers), use the API tools directly — they're already registered."""

NO_CREDENTIALS_SECTION = """
## Not Authenticated
No API credentials were provided at startup. All tools are available but API calls will return 401.

**To authenticate:** The user needs to add credentials to their MCP server configuration and restart:
```json
{
  "env": {
    "BW_CLIENT_ID": "CLI-xxxxxxxx-xxxx-...",
    "BW_CLIENT_SECRET": "your-secret-here"
  }
}
```
Tell the user to add these to their MCP config and restart the server.

Alternatively, for new accounts: use Express Registration (createRegistration → sendVerificationCode → verifyRegistrationCode), then call setCredentials with the new credentials."""

MESSAGING_SECTION = """
## Sending a Message (step by step)

1. You need: a `from` number (your Bandwidth number, E.164), an `applicationId` (messaging application UUID), and the `to` number.
2. Check resource://config for BW_NUMBER and BW_MESSAGING_APPLICATION_ID. If not set, use the Numbers API tools to find numbers and applications on the account.
3. Call `createMessage` with `to`, `from`, `applicationId`, and `text`.
4. To check delivery: call `listMessages` to search message history.
5. To receive replies: call `getInboundMessages` to read incoming messages (requires hosted mode with callbacks configured)."""

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
## Making a Voice Call (step by step)

Read resource://config first. You need these values:
- `BW_ACCOUNT_ID` — your account ID (auto-discovered from credentials)
- `BW_VOICE_APPLICATION_ID` — the voice application ID (set by user in MCP config)
- `BW_NUMBER` — your Bandwidth phone number to call from (set by user in MCP config)
- `BW_MCP_BASE_URL` — this server's public URL (auto-set by tunnel or user config)

Then follow these steps exactly:

1. **Generate the BXML**: Call `generateBXML` with the verbs for what to say.
   - For a one-shot message (say something and hang up), use `auto_gather=False`:
     `generateBXML(verbs=[{"type": "SpeakSentence", "text": "Hello!", "voice": "julie"}, {"type": "Hangup"}], auto_gather=False)`
   - For a conversation (say something and listen for a response), use the default `auto_gather=True`:
     `generateBXML(verbs=[{"type": "SpeakSentence", "text": "How can I help?", "voice": "julie"}])`

2. **Create the call**: Call `createCall` with:
   - `accountId`: from BW_ACCOUNT_ID in config
   - `from`: from BW_NUMBER in config (E.164 format like +19195551234)
   - `to`: the destination number (E.164)
   - `applicationId`: from BW_VOICE_APPLICATION_ID in config
   - `answerUrl`: BW_MCP_BASE_URL + `/callbacks/voice/answer`

3. **Queue the BXML immediately**: Call `respondToCallback(call_id, bxml)` with the call ID from createCall's response and the BXML from step 1. Do this right away — the BXML is delivered when the callee picks up.

4. **For conversations**: After the greeting, poll `getCallbackEvents(event_type="voice.gather")` for the caller's speech. Generate new BXML with `generateBXML` and deliver it with `respondToCallback(call_id, bxml)` for each turn.

### BXML tips
- `auto_gather=True` (default) wraps SpeakSentence in Gather for barge-in (caller can interrupt).
- `auto_gather=False` for fire-and-forget messages (say something, then Hangup).
- Use `voice="julie"` for natural-sounding TTS.
- For structured input, add `input_type: "speech dtmf"` so callers can speak or press keys."""

CALLBACK_SECTION = """
## Inbound Events & Callbacks
- **getInboundMessages**: Get recent inbound SMS/MMS received by your number.
- **getCallbackEvents**: Get all callback events (voice + messaging), filterable by type, call ID, or phone number.
- **configureCallbacks**: Point a Bandwidth application's webhook URLs at this server. Self-configuring — one call and webhooks are wired."""

REPORTING_SECTION = """
## Reporting & Analytics
Requires: BW_ACCOUNT_ID
- **createReport** → **getReportStatus** → **getReportFile**: Async report generation. Create, poll status, then download."""

CREDENTIALS_SECTION = """
## Session Management
- **clearCredentials**: Log out — clears stored credentials and access token. Authenticated tools will return 401 until you call **setCredentials** again."""

REGISTRATION_SECTION = """
## Express Registration (No Auth Required)
- **createRegistration**: Start a new Bandwidth account.
- **sendVerificationCode**: Send SMS verification to the registered number.
- **verifyRegistrationCode**: Confirm the code.
Then call **setCredentials** with the new client_id and client_secret to unlock authenticated tools."""

ERROR_SECTION = """
## Error Patterns
- **401 Unauthorized**: Wrong credentials. Check BW_CLIENT_ID/BW_CLIENT_SECRET.
- **422 Validation Error**: Missing or malformed fields. Phone numbers must be E.164 (+19195551234). Application IDs are UUIDs.
- **"Tool not found"**: Check BW_MCP_TOOLS / BW_MCP_EXCLUDE_TOOLS filters.
- **"Pending" responses**: Lookup and reporting are async — poll the status tool, don't treat pending as failure.
- **No authenticated tools**: Credentials weren't set. Use Express Registration flow or set env vars."""


# Mapping: if ANY of these tools are loaded, include the section
_SECTION_TRIGGERS: list[tuple[list[str], str]] = [
    (
        ["createRegistration", "sendVerificationCode", "verifyRegistrationCode"],
        REGISTRATION_SECTION,
    ),
    (["createMessage", "listMessages", "createMultiChannelMessage"], MESSAGING_SECTION),
    (
        [
            "createLookup",
            "getLookupStatus",
            "createSyncLookup",
            "createAsyncBulkLookup",
        ],
        LOOKUP_SECTION,
    ),
    (["generateMessagingCode", "generateVoiceCode", "verifyCode"], MFA_SECTION),
    (["createCall", "generateBXML", "respondToCallback"], VOICE_SECTION),
    (
        ["getInboundMessages", "getCallbackEvents", "configureCallbacks"],
        CALLBACK_SECTION,
    ),
    (["createReport", "getReportStatus", "getReportFile"], REPORTING_SECTION),
    (["clearCredentials"], CREDENTIALS_SECTION),
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

    if not config.get("BW_ACCESS_TOKEN"):
        sections.append(NO_CREDENTIALS_SECTION)

    tool_set = set(loaded_tools)
    for trigger_tools, section in _SECTION_TRIGGERS:
        if tool_set & set(trigger_tools):
            sections.append(section)

    sections.append(ERROR_SECTION)

    return "\n".join(sections)
