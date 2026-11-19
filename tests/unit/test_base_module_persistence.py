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
        from adapt.execution.nodes.projection import ProjectionModule

        assert ProjectionModule.persistence == ()

    def test_decision_logging_modules_declare_only_decision_writes(self):
        """Detection writes no product; its only persistence is the decision log."""
        from adapt.contracts import DecisionTableWrite
        from adapt.execution.nodes.detection import DetectModule

        assert {type(s) for s in DetectModule.persistence} == {DecisionTableWrite}
        assert DetectModule.persistence[0].key == "detection_decisions"

    def test_persisting_modules_declare_specs(self):
        from adapt.contracts import (
            DecisionTableWrite,
            NetcdfArtifact,
            ProductTableWrite,
            ProductView,
        )
        from adapt.execution.nodes.analysis import AnalysisModule
        from adapt.execution.nodes.cell_volume_stats import CellVolumeStatsModule
        from adapt.execution.nodes.ingest import LoadModule
        from adapt.execution.nodes.tracking import TrackingModule

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
        from adapt.execution.nodes.tracking import TrackingModule

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
