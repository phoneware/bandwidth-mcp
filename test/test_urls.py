"""Tests for src/urls.py — host resolution."""

import os
import pytest

import urls


def _clear_env(monkeypatch):
    for k in [
        "BW_API_URL",
        "BW_VOICE_URL",
        "BW_DASHBOARD_URL",
        "BW_MESSAGING_URL",
    ]:
        monkeypatch.delenv(k, raising=False)


def test_production_defaults(monkeypatch):
    _clear_env(monkeypatch)
    assert urls.api_base() == "https://api.bandwidth.com"
    assert urls.voice_base() == "https://voice.bandwidth.com"
    assert urls.dashboard_base() == "https://dashboard.bandwidth.com"
    assert urls.messaging_base() == "https://messaging.bandwidth.com"


def test_override_api(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_API_URL", "https://example.invalid")
    assert urls.api_base() == "https://example.invalid"
    # Other hosts unaffected.
    assert urls.voice_base() == "https://voice.bandwidth.com"


def test_override_trailing_slash_trimmed(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_DASHBOARD_URL", "https://example.invalid/")
    assert urls.dashboard_base() == "https://example.invalid"


def test_each_host_has_independent_override(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_API_URL", "https://a.invalid")
    monkeypatch.setenv("BW_VOICE_URL", "https://v.invalid")
    monkeypatch.setenv("BW_DASHBOARD_URL", "https://d.invalid")
    monkeypatch.setenv("BW_MESSAGING_URL", "https://m.invalid")
    assert urls.api_base() == "https://a.invalid"
    assert urls.voice_base() == "https://v.invalid"
    assert urls.dashboard_base() == "https://d.invalid"
    assert urls.messaging_base() == "https://m.invalid"


def test_empty_override_falls_through_to_default(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_API_URL", "")
    assert urls.api_base() == "https://api.bandwidth.com"


def test_oauth_token_url_uses_api_base(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_API_URL", "https://override.invalid")
    assert urls.oauth_token_url() == "https://override.invalid/api/v1/oauth2/token"


def test_dashboard_api_base_uses_dashboard_base(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("BW_DASHBOARD_URL", "https://override.invalid")
    assert urls.dashboard_api_base() == "https://override.invalid/api"


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
