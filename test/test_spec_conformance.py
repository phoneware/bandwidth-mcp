"""Spec conformance tests for the Bandwidth MCP tool registry and curated tools."""

import pytest
from registry import get_registry


def test_registry_operation_invariants():
    """Verify that every operation across all specs adheres to the MCP contract."""
    reg = get_registry()
    ops = reg.all_operations
    assert len(ops) >= 400

    valid_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}

    for name, op in ops.items():
        assert op.name == name
        assert (
            op.title and len(op.title.strip()) > 0
        ), f"{name} must have a non-empty title"
        assert (
            op.description and len(op.description.strip()) > 0
        ), f"{name} must have a description"
        assert op.method in valid_methods, f"{name} has invalid HTTP method {op.method}"
        assert op.path.startswith("/"), f"{name} path must start with a slash"
        assert op.annotations is not None, f"{name} must carry annotations"

        # Check write and read-only invariants
        if op.is_write:
            assert (
                op.annotations.read_only_hint is False
            ), f"Write op {name} must have read_only_hint=False"
        else:
            assert (
                op.annotations.read_only_hint is True
            ), f"Read op {name} must have read_only_hint=True"

        # Check destructive invariants
        if op.is_destructive:
            assert (
                op.annotations.destructive_hint is True
            ), f"Destructive op {name} must have destructive_hint=True"

        # Numbers operations must be XML
        if op.spec_name == "numbers":
            assert (
                op.is_xml is True
            ), f"Numbers op {name} must be flagged as is_xml=True"
        else:
            assert (
                op.is_xml is False
            ), f"Non-numbers op {name} must not be flagged as is_xml=True"


def test_write_composites_schema_conformance():
    """Verify that write operations declared in numbers spec declare valid schema properties."""
    reg = get_registry()

    # Check CreateSite schema
    create_site = reg.get_operation("numbers.CreateSite")
    assert create_site is not None
    assert create_site.request_body_schema is not None
    site_props = create_site.request_body_schema.get("properties", {})
    assert "Name" in site_props
    assert "Address" in site_props

    # Check CreateOrder schema
    create_order = reg.get_operation("numbers.CreateOrder")
    assert create_order is not None
    assert create_order.request_body_schema is not None

    # Check CreateLidbOrder schema
    create_lidb = reg.get_operation("numbers.CreateLidbOrder")
    assert create_lidb is not None
    assert create_lidb.request_body_schema is not None
