# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Persistence spec types are frozen value types with zero dependencies."""

import dataclasses

import pytest

pytestmark = pytest.mark.unit


def test_specs_are_frozen_value_types():
    from adapt.contracts import NetcdfArtifact, ProductTableWrite

    spec = ProductTableWrite(key="cell_stats", table="cell_stats", primary_key=("run_id",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.key = "other"
    nc = NetcdfArtifact(key="ds", product_type="gridded3d", description="d")
    with pytest.raises(dataclasses.FrozenInstanceError):
        nc.key = "other"
    assert ProductTableWrite(key="r", table="t", primary_key=("run_id",)).index_columns == ()


def test_a_view_names_its_row_table_and_needs_nothing_else():
    from adapt.contracts import ProductView

    view = ProductView(name="cells_by_scan", rows_from="cell_identity")

    assert view.computed == ()
    assert view.reads == ()


def test_a_store_read_names_its_key_its_source_and_how_far_it_reads():
    from adapt.contracts import StoreRead

    read = StoreRead(key="cells_so_far", source="cells_by_scan", up_to_scan=True)

    assert (read.key, read.source, read.up_to_scan) == ("cells_so_far", "cells_by_scan", True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        read.source = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        StoreRead(key="cells_so_far", source="cells_by_scan")  # type: ignore[call-arg]


def test_all_spec_types_exported():
    from adapt import contracts

    for name in (
        "ProductTableWrite",
        "NetcdfArtifact",
        "ProductView",
        "DecisionTableWrite",
        "PersistenceSpec",
        "PersistenceMeta",
        "StoreRead",
    ):
        assert hasattr(contracts, name), f"adapt.contracts missing {name}"


def test_contracts_persistence_imports_stdlib_only():
    import ast

    import adapt.contracts.persistence as mod

    with open(mod.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"dataclasses", "datetime"}, f"non-stdlib imports: {imported}"
