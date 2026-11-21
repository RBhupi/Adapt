# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests for BaseModule.persistence declaration.

Modules that persist outputs declare a tuple of persistence specs (from
adapt.contracts); modules that persist nothing leave the default empty tuple.
"""

import pytest

pytestmark = pytest.mark.unit

from adapt.modules.base import BaseModule  # noqa: E402


class _NoPersistenceModule(BaseModule):
    name = "no_persistence"

    def run(self, context: dict) -> dict:
        return {}


class TestBaseModulePersistence:
    def test_default_persistence_is_empty(self):
        assert _NoPersistenceModule.persistence == ()

    def test_pure_compute_modules_declare_no_persistence(self):
        from adapt.modules.projection.node import ProjectionModule

        assert ProjectionModule.persistence == ()

    def test_decision_logging_modules_declare_only_decision_writes(self):
        """Detection writes no product; its only persistence is the decision log."""
        from adapt.contracts import DecisionTableWrite
        from adapt.modules.detection.node import DetectModule

        assert {type(s) for s in DetectModule.persistence} == {DecisionTableWrite}
        assert DetectModule.persistence[0].key == "detection_decisions"

    def test_persisting_modules_declare_specs(self):
        from adapt.contracts import (
            DecisionTableWrite,
            NetcdfArtifact,
            ProductTableWrite,
            ProductView,
        )
        from adapt.modules.analysis.node import AnalysisModule
        from adapt.modules.cell_volume_stats.node import CellVolumeStatsModule
        from adapt.modules.ingest.node import LoadModule
        from adapt.modules.tracking.node import TrackingModule

        assert {type(s) for s in LoadModule.persistence} == {NetcdfArtifact}
        assert {type(s) for s in AnalysisModule.persistence} == {ProductTableWrite}
        stats, adjacency = AnalysisModule.persistence
        assert stats.table == "cell_stats"
        assert stats.primary_key == ("run_id", "scan_id", "cell_label")
        assert adjacency.table == "cell_adjacency"
        assert adjacency.primary_key == ("run_id", "scan_id", "cell_label_a", "cell_label_b")
        assert {type(s) for s in TrackingModule.persistence} == {
            NetcdfArtifact,
            ProductTableWrite,
            ProductView,
            DecisionTableWrite,
        }
        tables = [s.table for s in TrackingModule.persistence if isinstance(s, ProductTableWrite)]
        assert tables == ["cell_identity", "cell_events", "cell_tracks"]
        assert {type(s) for s in CellVolumeStatsModule.persistence} == {ProductTableWrite}

    def test_the_cell_view_names_only_tracking_tables(self):
        """Tracking gives the view its rows and three flags; the tables of other
        modules join it by their key, without tracking naming them."""
        from adapt.contracts import ProductTableWrite, ProductView
        from adapt.modules.tracking.node import TrackingModule

        (view,) = (s for s in TrackingModule.persistence if isinstance(s, ProductView))
        own = {s.table for s in TrackingModule.persistence if isinstance(s, ProductTableWrite)}

        assert view.name == "cells_by_scan"
        assert view.rows_from == "cell_identity"
        assert {view.rows_from, *view.reads} <= own
        assert [name for name, _ in view.computed] == [
            "is_split_source_here",
            "is_merge_source_here",
            "is_terminated_after_here",
        ]


def _record_type(bundle_type: type, part: str) -> type:
    """The record type of a bundle part: the field's type, or the item type of a tuple."""
    import typing

    hint = typing.get_type_hints(bundle_type)[part]
    return typing.get_args(hint)[0] if typing.get_origin(hint) is tuple else hint


def _int_and_bool_fields(record_type: type) -> tuple[str, ...]:
    import typing
    from dataclasses import fields

    hints = typing.get_type_hints(record_type)
    return tuple(
        f.name
        for f in fields(record_type)
        if hints[f.name] in (int, bool) or set(typing.get_args(hints[f.name])) & {int, bool}
    )


def _assert_decision_tables(module, bundle_type: type, key: str, expected: dict) -> None:
    from adapt.contracts import DecisionTableWrite

    specs = [s for s in module.persistence if isinstance(s, DecisionTableWrite)]
    assert [s.table for s in specs] == list(expected)
    for spec in specs:
        records, primary_key, index_columns, sequenced = expected[spec.table]
        assert spec.key == key
        assert spec.records == records
        assert spec.primary_key == primary_key
        assert spec.index_columns == index_columns
        assert spec.sequenced is sequenced
        own = _int_and_bool_fields(_record_type(bundle_type, records))
        assert spec.integer_columns == (("seq", *own) if sequenced else own), spec.table


class TestDecisionTables:
    def test_detection_declares_its_three_decision_tables(self):
        from adapt.contracts import SegmentationDecisions
        from adapt.modules.detection.node import DetectModule

        _assert_decision_tables(
            DetectModule,
            SegmentationDecisions,
            "detection_decisions",
            {
                "segmentation_frames": ("frame", ("run_id", "scan_id"), (), False),
                "segmentation_seeds": ("seeds", ("run_id", "scan_id", "seq"), (), True),
                "segmentation_components": (
                    "components",
                    ("run_id", "scan_id", "component_id"),
                    (),
                    False,
                ),
            },
        )

    def test_tracking_declares_its_six_decision_tables(self):
        from adapt.contracts import TrackingDecisions
        from adapt.modules.tracking.node import TrackingModule

        _assert_decision_tables(
            TrackingModule,
            TrackingDecisions,
            "tracking_decisions",
            {
                "tracking_scans": ("scan", ("run_id", "scan_id"), (), False),
                "tracking_cells": (
                    "cells",
                    ("run_id", "scan_id", "cell_label"),
                    ("cell_uid",),
                    False,
                ),
                "tracking_pairs": (
                    "pairs",
                    ("run_id", "scan_id", "prev_cell_uid", "curr_cell_label"),
                    ("prev_cell_uid",),
                    False,
                ),
                "tracking_lineage": ("lineage", ("run_id", "scan_id", "seq"), (), True),
                "tracking_identity": (
                    "identity",
                    (
                        "run_id",
                        "scan_id",
                        "kind",
                        "identity_uid",
                        "candidate_side",
                        "candidate_label",
                    ),
                    (),
                    False,
                ),
                "tracking_latent": ("latent", ("run_id", "scan_id", "cell_uid"), (), False),
            },
        )
