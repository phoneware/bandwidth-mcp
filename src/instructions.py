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

To call someone, follow these steps exactly:

1. **Find your from number and voice application**: Call `listCalls` or check resource://config for BW_NUMBER. If you don't have an application ID, call the Numbers API tools to list applications on the account.
2. **Configure callbacks** (if not already done): Call `configureCallbacks(application_id, base_url)` where base_url is this server's public URL. This points the voice application's webhooks at this server.
3. **Generate the greeting BXML**: Call `generateBXML` with the verbs for what to say. Example:
   ```
   generateBXML(verbs=[{"type": "SpeakSentence", "text": "Hello! How is your day going?", "voice": "julie"}])
   ```
   auto_gather defaults to True, which wraps SpeakSentence in Gather so the caller can respond.
4. **Create the call**: Call `createCall` with `from` (your Bandwidth number, E.164), `to` (destination, E.164), `applicationId`, and `answerUrl` (this server's callback URL, e.g. `{base_url}/callbacks/voice/answer`).
5. **Handle the conversation**: Poll `getCallbackEvents(event_type="voice.gather")` for caller responses. For each response, generate new BXML with `generateBXML` and deliver it with `respondToCallback(call_id, bxml)`.

### Key voice tools
- **createCall**: Initiate an outbound call.
- **generateBXML**: Produce valid BXML from verb descriptions (SpeakSentence, Gather, Transfer, Record, Pause, Hangup, Redirect, etc.).
- **respondToCallback**: Queue BXML for an active call. First-write-wins for multi-session safety.
- **getCallbackEvents**: Read voice events (gather results with transcribed speech, call status, etc.).
- **configureCallbacks**: Wire a Bandwidth application's webhook URLs to this server.

### BXML tips
- auto_gather=True (default) wraps SpeakSentence in Gather for barge-in.
- Use input_type "speech dtmf" so callers can speak or press keys.
- Use voice="julie" or other Bandwidth TTS voices."""

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
