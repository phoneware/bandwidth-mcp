import pytest
from src.instructions import build_instructions


def test_build_instructions_includes_header():
    """Instructions always start with the server identity."""
    result = build_instructions(config={}, loaded_tools=[])
    assert "Bandwidth MCP Server" in result


def test_build_instructions_includes_messaging_when_tools_loaded():
    """Messaging section appears when messaging tools are loaded."""
    result = build_instructions(
        config={}, loaded_tools=["createMessage", "listMessages"]
    )
    assert "createMessage" in result
    assert "SMS" in result or "send" in result.lower()


def test_build_instructions_excludes_messaging_when_no_tools():
    """Messaging section absent when no messaging tools loaded."""
    result = build_instructions(config={}, loaded_tools=["createLookup"])
    assert "createMessage" not in result


def test_build_instructions_includes_no_credentials_warning():
    """Warning appears when no username in config."""
    result = build_instructions(config={}, loaded_tools=[])
    assert "setCredentials" in result or "Express Registration" in result


def test_build_instructions_no_warning_when_credentials_present():
    """No credential warning when access token is set."""
    result = build_instructions(
        config={"BW_ACCESS_TOKEN": "bearer-token-here"},
        loaded_tools=["createMessage"],
    )
    assert "no credentials" not in result.lower()


def test_build_instructions_includes_lookup_section():
    """Lookup section appears when lookup tools loaded."""
    result = build_instructions(
        config={}, loaded_tools=["createLookup", "getLookupStatus"]
    )
    assert "createLookup" in result
    assert "getLookupStatus" in result


def test_build_instructions_includes_voice_section():
    """Voice section appears when voice tools loaded."""
    result = build_instructions(config={}, loaded_tools=["createCall", "generateBXML"])
    assert "createCall" in result
    assert "BXML" in result


def test_build_instructions_includes_mfa_section():
    """MFA section appears when MFA tools loaded."""
    result = build_instructions(
        config={}, loaded_tools=["generateMessagingCode", "verifyCode"]
    )
    assert "generateMessagingCode" in result
    assert "verifyCode" in result


def test_build_instructions_includes_callback_section():
    """Callback section appears when callback tools loaded."""
    result = build_instructions(
        config={}, loaded_tools=["getCallbackEvents", "getInboundMessages"]
    )
    assert "getCallbackEvents" in result


def test_build_instructions_includes_error_patterns():
    """Error patterns section is always included."""
    result = build_instructions(config={}, loaded_tools=[])
    assert "401" in result
    assert "422" in result
    assert "E.164" in result
