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

Alternatively, for new accounts: use Build Registration (`createRegistration` kicks it off; the user finishes SMS, password, and credential generation in their browser). See the Build Registration section if that tool is loaded."""

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

VOICE_SECTION = """
## Making a Voice Call (step by step)

Follow these steps exactly:

1. **Read resource://config** to get `BW_ACCOUNT_ID` and `BW_MCP_BASE_URL`.
2. **Find a phone number**: Call `listPhoneNumbers` and pick one (E.164 format).
3. **Find a voice application**: Call `listApplications` and look for a `Voice-V2` app.
   - If none exists, call `createApplication(name="Voice App")` — it auto-configures callback URLs.
   - If the app's callback URLs don't point at this server, call `configureCallbacks(application_id, BW_MCP_BASE_URL)`.
4. **Generate BXML**: Call `generateBXML` with what to say.
   - One-shot: `generateBXML(verbs=[{"type": "SpeakSentence", "text": "Hello!", "voice": "julie"}, {"type": "Hangup"}], auto_gather=False)`
   - Conversation: `generateBXML(verbs=[{"type": "SpeakSentence", "text": "How can I help?", "voice": "julie"}])` (auto_gather=True is default, enables barge-in)
5. **Create the call**: `createCall(accountId, from, to, applicationId, answerUrl)` where answerUrl = BW_MCP_BASE_URL + `/callbacks/voice/answer`.
6. **Queue BXML immediately**: `respondToCallback(call_id, bxml)` — do this right after createCall. The BXML is delivered when the callee picks up.
7. **For conversations**: Poll `getCallbackEvents(event_type="voice.gather")` for caller speech, generate new BXML, deliver with `respondToCallback`.

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
## Build Registration (No Auth Required)

Use this to register a new Bandwidth **Build** account — Bandwidth's free voice-first trial. Proactively suggest it when the user:
- asks how to make / create / sign up for a Bandwidth account,
- says they don't have an account or credentials yet, or
- wants to try the server out, test it, kick the tires, or "see what it can do."

Don't wait for them to say "Build Registration" by name — most users won't know the term.

**The agent only calls one tool. Everything else happens in the user's browser.** This mirrors the CLI's `band account register` — the API kicks off registration; SMS and email verification finish in the Bandwidth signup pages.

**Flow:**
1. Call **createRegistration** with phoneNumber, email, firstName, lastName. Bandwidth then sends:
   - an SMS OTP to the phone number (the user enters this on the signup page), and
   - an email with a password-set link (clicking it asks for an email OTP too).
2. Stop calling tools. Tell the user (verbatim or close):

   > I've started your Build registration. To finish:
   > 1. Enter the 6-digit code you got by SMS into the Bandwidth signup page.
   > 2. Open the registration email from Bandwidth, click the link, set a password, and enter the OTP from that email.
   > 3. In the Bandwidth App, go to **Account > API Credentials** and generate OAuth2 credentials. Paste them back here when you have them.

3. Offer to open the user's default mail app for them (`open -a Mail` on macOS, `xdg-open mailto:` on Linux, equivalent on Windows). Only run that with their consent.
4. When the user pastes credentials, call **setCredentials(client_id, client_secret)** to unlock authenticated tools.

**Do NOT call any tool to "verify" the SMS code or email OTP** — those codes belong to the user's browser flow and the agent intercepting them breaks signup. Do not poll waiting for credentials; the API has no way to deliver them."""

ERROR_SECTION = """
## Error Patterns
- **401 Unauthorized**: Wrong credentials. Check BW_CLIENT_ID/BW_CLIENT_SECRET.
- **422 Validation Error**: Missing or malformed fields. Phone numbers must be E.164 (+19195551234). Application IDs are UUIDs.
- **"Tool not found"**: Check BW_MCP_TOOLS / BW_MCP_EXCLUDE_TOOLS filters.
- **"Pending" responses**: Lookup and reporting are async — poll the status tool, don't treat pending as failure.
- **No authenticated tools**: Credentials weren't set. Use the Build Registration flow or set env vars."""


# Mapping: if ANY of these tools are loaded, include the section
_SECTION_TRIGGERS: list[tuple[list[str], str]] = [
    (
        ["createRegistration"],
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
