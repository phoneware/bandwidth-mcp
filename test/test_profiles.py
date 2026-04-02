import pytest
from src.profiles import resolve_profile


def test_resolve_single_profile():
    tools = resolve_profile("messaging")
    assert "createMessage" in tools
    assert "listMessages" in tools


def test_resolve_combined_profiles():
    tools = resolve_profile("messaging,lookup")
    assert "createMessage" in tools
    assert "createSyncLookup" in tools


def test_resolve_full_profile():
    tools = resolve_profile("full")
    assert tools is None


def test_resolve_unknown_profile_raises():
    with pytest.raises(ValueError, match="Unknown profile"):
        resolve_profile("nonexistent")


def test_resolve_none_returns_none():
    tools = resolve_profile(None)
    assert tools is None


def test_resolve_empty_string_returns_none():
    tools = resolve_profile("")
    assert tools is None


def test_voice_profile():
    tools = resolve_profile("voice")
    assert "createCall" in tools
    assert "generateBXML" in tools
    assert "getCallbackEvents" in tools


def test_onboarding_profile():
    tools = resolve_profile("onboarding")
    assert "createRegistration" in tools
    assert "setCredentials" in tools
