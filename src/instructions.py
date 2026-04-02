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
