"""Tool profile presets — task-oriented tool sets that keep context small.

Profiles answer "what do I need to do X?" not "give me every tool in this API."
The default (no profile) loads a curated set covering the common use cases.
"""

from typing import Optional

# Task-oriented profiles
PROFILES: dict[str, list[str]] = {
    "messaging": [
        "createMessage",
        "listMessages",
        "getInboundMessages",
        "configureCallbacks",
        "setCredentials",
    ],
    "voice": [
        "createCall",
        "updateCall",
        "updateCallBxml",
        "listCalls",
        "getCallState",
        "generateBXML",
        "respondToCallback",
        "getCallbackEvents",
        "configureCallbacks",
        "setCredentials",
        # Numbers discovery — need to find your from number and application
        "GetPhoneNumbers",
        "getPhoneNumbers",
        "ListApplications",
    ],
    "onboarding": [
        "createRegistration",
        "sendVerificationCode",
        "verifyRegistrationCode",
        "setCredentials",
    ],
    "lookup": [
        "createSyncLookup",
        "createAsyncBulkLookup",
        "getAsyncBulkLookup",
        "setCredentials",
    ],
    "mfa": [
        "generateMessagingCode",
        "generateVoiceCode",
        "verifyCode",
        "setCredentials",
    ],
    "numbers": [
        # Number discovery and management
        "GetPhoneNumbers",
        "getPhoneNumbers",
        "ListApplications",
        "GetApplication",
        "SearchAvailableNumbers",
        "CreateOrder",
        "GetOrder",
        "ListOrders",
        "DisconnectNumbers",
        "setCredentials",
    ],
}

# Default: what most users need without specifying a profile
DEFAULT_TOOLS = list(dict.fromkeys(
    PROFILES["messaging"]
    + PROFILES["voice"]
    + PROFILES["lookup"]
    + PROFILES["mfa"]
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

    return list(dict.fromkeys(tools))
