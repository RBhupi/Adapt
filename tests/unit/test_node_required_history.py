# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests that execution nodes declare correct required_history values.

Only projection needs a prior scan (required_history=2). Analysis and tracking
need none of their own: they are skipped exactly when projection, upstream of
them, is skipped.
No in-pipeline node should use pipeline_phase=1 or pipeline_phase=2.
"""

import pytest

pytestmark = pytest.mark.unit


class TestNodeRequiredHistory:
    def test_ingest_node_requires_single_scan(self):
        from adapt.execution.nodes.ingest import LoadModule

        assert LoadModule.required_history == 1

    def test_detection_node_requires_single_scan(self):
        from adapt.execution.nodes.detection import DetectModule

        assert DetectModule.required_history == 1

    def test_projection_node_requires_two_scans(self):
        from adapt.execution.nodes.projection import ProjectionModule

        assert ProjectionModule.required_history == 2

    def test_analysis_node_needs_no_history_of_its_own(self):
        from adapt.execution.nodes.analysis import AnalysisModule

        assert AnalysisModule.required_history == 1

    def test_tracking_node_needs_no_history_of_its_own(self):
        from adapt.execution.nodes.tracking import TrackingModule

        assert TrackingModule.required_history == 1

    def test_no_in_pipeline_node_uses_pipeline_phase_one(self):
        """pipeline_phase=1 is retired — all in-pipeline nodes use pipeline_phase=0."""
        from adapt.execution.nodes.detection import DetectModule
        from adapt.execution.nodes.ingest import LoadModule

        assert LoadModule.pipeline_phase == 0
        assert DetectModule.pipeline_phase == 0

    def test_no_in_pipeline_node_uses_pipeline_phase_two(self):
        """pipeline_phase=2 is retired — all in-pipeline nodes use pipeline_phase=0."""
        from adapt.execution.nodes.analysis import AnalysisModule
        from adapt.execution.nodes.projection import ProjectionModule
        from adapt.execution.nodes.tracking import TrackingModule

        assert ProjectionModule.pipeline_phase == 0
        assert AnalysisModule.pipeline_phase == 0
        assert TrackingModule.pipeline_phase == 0
