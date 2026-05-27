"""Tests for src/urls.py — host resolution."""

import urls


def _clear_env(monkeypatch):
    for k in [
        "BW_ENVIRONMENT",
        "BW_API_URL",
        "BW_VOICE_URL",
        "BW_MESSAGING_URL",
        "BW_MFA_URL",
        "BW_INSIGHTS_URL",
    ]:
        monkeypatch.delenv(k, raising=False)


def test_production_defaults(monkeypatch):
    _clear_env(monkeypatch)
    assert urls.api_base() == "https://api.bandwidth.com"
    assert urls.voice_base() == "https://voice.bandwidth.com"
    assert urls.messaging_base() == "https://messaging.bandwidth.com"


def test_override_api(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_API_URL", "https://example.invalid")
    assert urls.api_base() == "https://example.invalid"
    # Other hosts unaffected.
    assert urls.voice_base() == "https://voice.bandwidth.com"


def test_override_trailing_slash_trimmed(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_API_URL", "https://example.invalid/")
    assert urls.api_base() == "https://example.invalid"


def test_each_host_has_independent_override(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_API_URL", "https://a.invalid")
    monkeypatch.setenv("BW_VOICE_URL", "https://v.invalid")
    monkeypatch.setenv("BW_MESSAGING_URL", "https://m.invalid")
    assert urls.api_base() == "https://a.invalid"
    assert urls.voice_base() == "https://v.invalid"
    assert urls.messaging_base() == "https://m.invalid"


def test_empty_override_falls_through_to_default(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_API_URL", "")
    assert urls.api_base() == "https://api.bandwidth.com"


def test_oauth_token_url_uses_api_base(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_API_URL", "https://override.invalid")
    assert urls.oauth_token_url() == "https://override.invalid/api/v1/oauth2/token"


def test_dashboard_api_base_uses_api_base(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_API_URL", "https://override.invalid")
    assert urls.dashboard_api_base() == "https://override.invalid/api/v2"


def test_environment_test_flips_api_and_voice(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_ENVIRONMENT", "test")
    assert urls.api_base() == "https://test.api.bandwidth.com"
    assert urls.voice_base() == "https://test.voice.bandwidth.com"
    # Messaging is the same host in test, matching the CLI.
    assert urls.messaging_base() == "https://messaging.bandwidth.com"


def test_environment_uat_alias(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_ENVIRONMENT", "uat")
    assert urls.api_base() == "https://test.api.bandwidth.com"


def test_environment_is_case_insensitive(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_ENVIRONMENT", "TEST")
    assert urls.api_base() == "https://test.api.bandwidth.com"


def test_per_host_override_wins_over_environment(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_ENVIRONMENT", "test")
    monkeypatch.setenv("BW_API_URL", "https://custom.invalid")
    assert urls.api_base() == "https://custom.invalid"
    # Voice still flips to the test default.
    assert urls.voice_base() == "https://test.voice.bandwidth.com"


def test_unknown_environment_falls_back_to_prod(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_ENVIRONMENT", "staging")
    assert urls.api_base() == "https://api.bandwidth.com"


def test_no_inlined_hosts_in_src(monkeypatch):
    """Source under src/ must not inline any production hosts as string literals
    outside of urls.py and OpenAPI spec metadata."""
    import pathlib

    root = pathlib.Path(__file__).parent.parent / "src"
    forbidden = [
        "https://api.bandwidth.com",
        "https://voice.bandwidth.com",
        "https://dashboard.bandwidth.com",
        "https://messaging.bandwidth.com",
        "https://test.api.bandwidth.com",
        "https://test.voice.bandwidth.com",
    ]
    allowed_files = {"urls.py"}
    offenders = []
    for py in root.rglob("*.py"):
        if py.name in allowed_files:
            continue
        text = py.read_text()
        for needle in forbidden:
            if needle in text:
                offenders.append(f"{py}: {needle}")
    assert not offenders, "Inlined host strings found:\n" + "\n".join(offenders)


def test_mfa_and_insights_bases(monkeypatch):
    _clear_env(monkeypatch)
    assert urls.mfa_base() == "https://mfa.bandwidth.com"
    assert urls.insights_base() == "https://insights.bandwidth.com"


def test_mfa_url_override(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_MFA_URL", "https://stage.mfa.invalid")
    assert urls.mfa_base() == "https://stage.mfa.invalid"


def test_swap_host_unchanged_for_unknown_host(monkeypatch):
    _clear_env(monkeypatch)
    assert urls.swap_host("https://example.com/foo") == "https://example.com/foo"


def test_swap_host_applies_env_override(monkeypatch):
    """swap_host rewrites the host portion of a spec server URL while preserving the path."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_VOICE_URL", "https://stage.voice.invalid")
    assert (
        urls.swap_host("https://voice.bandwidth.com/api/v2")
        == "https://stage.voice.invalid/api/v2"
    )


def test_swap_host_uses_environment_mapping(monkeypatch):
    """BW_ENVIRONMENT=test flips known hosts to their test equivalents."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_ENVIRONMENT", "test")
    assert (
        urls.swap_host("https://api.bandwidth.com/v2")
        == "https://test.api.bandwidth.com/v2"
    )
    assert (
        urls.swap_host("https://voice.bandwidth.com/api/v2")
        == "https://test.voice.bandwidth.com/api/v2"
    )


def test_swap_host_per_host_override_wins(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_ENVIRONMENT", "test")
    monkeypatch.setenv("BW_API_URL", "https://override.invalid")
    assert (
        urls.swap_host("https://api.bandwidth.com/v2/foo")
        == "https://override.invalid/v2/foo"
    )


def test_swap_host_messaging_stays_prod_in_test_env(monkeypatch):
    """BW_ENVIRONMENT=test keeps messaging on prod (matches CLI behavior)."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_ENVIRONMENT", "test")
    assert (
        urls.swap_host("https://messaging.bandwidth.com/api/v2")
        == "https://messaging.bandwidth.com/api/v2"
    )
