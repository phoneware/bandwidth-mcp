"""Tool profiles — curated tool sets mirroring the CLI's command surface.

Instead of loading entire OpenAPI specs (430+ tools), we cherrypick the
operationIds that map to real CLI commands. This keeps context small and
matches the agent experience to the CLI experience.
"""

from typing import Optional

# Cherrypicked operationIds mapped from the CLI command structure.
# Format: CLI command → operationId(s) from the OpenAPI specs.

PROFILES: dict[str, list[str]] = {
    # bw call create/list/get/update/hangup
    "voice": [
        "createCall",
        "listCalls",
        "getCallState",
        "updateCall",
        "updateCallBxml",
        # Custom tools
        "generateBXML",
        "respondToCallback",
        "getCallbackEvents",
        "configureCallbacks",
        # Discovery — find your number and app
        "listPhoneNumbers",
        "listApplications",
        "createApplication",
    ],
    # bw call recording list/get/delete/download + transcription
    "recordings": [
        "listCallRecordings",
        "getCallRecording",
        "deleteRecording",
        "downloadCallRecording",
        "transcribeCallRecording",
        "getRecordingTranscription",
    ],
    # Numbers/Dashboard API (XML) — hand-written read-only adapter in
    # tools/numbers.py (from_openapi can't drive the XML API). Port-in (LNP)
    # orders, available-number search, order history, sites.
    "numbers": [
        "listPortInOrders",
        "getPortInOrder",
        "getPortInNotes",
        "listPortOutOrders",
        "getPortOutOrder",
        "searchAvailableNumbers",
        "listNumberOrders",
        "getNumberOrder",
        "listSites",
        "listSipPeers",
        "getPhoneNumberDetail",
        "checkPortability",
        # Inventory + application listing also live in the voice profile;
        # included here so numbers-only deployments keep them (Phoneware's
        # deployment drops voice: its creds have no Bandwidth Voice access).
        "listPhoneNumbers",
        "listApplications",
    ],
    # Carrier WRITES: buy, remove, and port real service. Deployed only
    # where the operator explicitly opts in.
    "numbers-write": [
        "orderPhoneNumbers",
        "disconnectPhoneNumbers",
        "createPortInOrder",
        "supplementPortInOrder",
        "cancelPortInOrder",
    ],
    # Usage/billing reports via the async /reports engine.
    "billing": [
        "listReports",
        "listReportInstances",
        "createReportInstance",
        "getReportInstance",
        "downloadReportFile",
    ],
    # "applications": [...],
    # "locations": [...],
    # bw account register (Build registration). Only the kickoff is exposed —
    # SMS and email verification happen in the user's browser, not via API.
    "onboarding": [
        "createRegistration",
    ],
    # createMessage/listMessages + media
    "messaging": [
        "createMessage",
        "listMessages",
        "listMedia",
        "getMedia",
        "uploadMedia",
        "deleteMedia",
        "getInboundMessages",
        "configureCallbacks",
    ],
    # Phone number lookup
    "lookup": [
        "createSyncLookup",
        "createAsyncBulkLookup",
        "getAsyncBulkLookup",
    ],
}

# Always included regardless of profile
_ALWAYS_TOOLS = ["setCredentials", "clearCredentials", "listAccounts"]

# Default: voice + messaging + lookup
DEFAULT_TOOLS = list(dict.fromkeys(
    PROFILES["voice"]
    + PROFILES["messaging"]
    + PROFILES["lookup"]
    + _ALWAYS_TOOLS
))


def resolve_profile(profile_str: Optional[str]) -> Optional[list[str]]:
    """Resolve a profile string into a list of tool names.

    Returns:
        List of tool names, or None for 'full' (all tools).
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
        if name == "default":
            tools.extend(DEFAULT_TOOLS)
        elif name not in PROFILES:
            available = ", ".join(sorted(PROFILES.keys()))
            raise ValueError(
                f"Unknown profile: '{name}'. Available: {available}, default, full"
            )
        else:
            tools.extend(PROFILES[name])

    tools.extend(_ALWAYS_TOOLS)
    return list(dict.fromkeys(tools))
