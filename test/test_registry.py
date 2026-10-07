"""Tests for the unified API registry."""

from pathlib import Path
import pytest

from registry import ApiRegistry, get_registry, EXCLUDED_OPERATIONS


def test_registry_loads_all_specs():
    reg = get_registry()
    ops = reg.all_operations
    assert len(ops) >= 400

    # Ensure all specs are represented
    specs = {op.spec_name for op in ops.values()}
    expected_specs = {
        "numbers",
        "voice",
        "messaging",
        "insights",
        "lookup",
        "end-user-management",
        "toll-free-verification",
    }
    assert expected_specs.issubset(specs)


def test_credentials_operations_are_stripped():
    reg = get_registry()
    ops = reg.all_operations

    assert "numbers.ListSipCredentialsOnRealm" not in ops
    assert "numbers.CreateSipCredentialsOnRealm" not in ops
    assert "numbers.DeleteSipCredentialOnRealm" not in ops
    assert "numbers.RetrieveSipCredentials" not in ops
    assert "numbers.CreateSipCredentials" not in ops
    assert "numbers.DeleteSipCredentials" not in ops
    for excl in EXCLUDED_OPERATIONS:
        assert (
            excl not in ops
        ), f"Operation {excl} should have been stripped from registry"


def test_namespacing_prevents_collision():
    reg = get_registry()
    voice_call = reg.get_operation("voice.listCalls")
    insights_call = reg.get_operation("insights.listCalls")

    assert voice_call is not None
    assert insights_call is not None
    assert voice_call.name == "voice.listCalls"
    assert insights_call.name == "insights.listCalls"
    assert voice_call.path == "/accounts/{accountId}/calls"
    assert insights_call.path == "/v1/voice/calls"


def test_aliases_resolve():
    reg = get_registry()
    op1 = reg.get_operation("phone-number-lookup.createSyncLookup")
    op2 = reg.get_operation("lookup.createSyncLookup")
    assert op1 is not None
    assert op2 is not None
    assert op1.name == op2.name


def test_search_ranking():
    reg = get_registry()
    results = reg.search("sites", limit=10)
    assert len(results) > 0

    names = [r["name"] for r in results]
    assert "numbers.ListSites" in names

    # Search for calls
    call_results = reg.search("calls", limit=10)
    call_names = [r["name"] for r in call_results]
    assert "voice.listCalls" in call_names or "insights.listCalls" in call_names


def test_classification_of_writes_and_destructive():
    reg = get_registry()

    list_sites = reg.get_operation("numbers.ListSites")
    assert list_sites is not None
    assert not list_sites.is_write
    assert not list_sites.is_destructive
    assert list_sites.annotations.read_only_hint is True

    create_site = reg.get_operation("numbers.CreateSite")
    assert create_site is not None
    assert create_site.is_write
    assert create_site.annotations.read_only_hint is False

    delete_site = reg.get_operation("numbers.DeleteSite")
    assert delete_site is not None
    assert delete_site.is_write
    assert delete_site.is_destructive
    assert delete_site.annotations.destructive_hint is True
