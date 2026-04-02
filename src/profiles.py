"""Tool profile presets for reducing context window pressure."""

from typing import Optional

PROFILES: dict[str, list[str]] = {
    "messaging": [
        "createMessage",
        "listMessages",
        "createMultiChannelMessage",
        "getInboundMessages",
        "getMessageStatus",
        "configureCallbacks",
        "listMedia",
        "getMedia",
        "uploadMedia",
        "deleteMedia",
    ],
    "voice": [
        "createCall",
        "generateBXML",
        "respondToCallback",
        "getCallbackEvents",
        "configureCallbacks",
        "setVoiceHandler",
    ],
    "onboarding": [
        "createRegistration",
        "sendVerificationCode",
        "verifyRegistrationCode",
        "setCredentials",
    ],
    "lookup": [
        "createLookup",
        "getLookupStatus",
        "createSyncLookup",
        "createAsyncBulkLookup",
        "getAsyncBulkLookup",
    ],
}


def resolve_profile(profile_str: Optional[str]) -> Optional[list[str]]:
    if not profile_str:
        return None
    names = [p.strip() for p in profile_str.split(",") if p.strip()]
    if not names:
        return None
    if "full" in names:
        return None
    tools: list[str] = []
    for name in names:
        if name not in PROFILES:
            raise ValueError(
                f"Unknown profile: '{name}'. Available: {', '.join(sorted(PROFILES.keys()))}, full"
            )
        tools.extend(PROFILES[name])
    seen: set[str] = set()
    unique: list[str] = []
    for t in tools:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique
