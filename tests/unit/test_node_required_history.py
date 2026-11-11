# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests that execution nodes declare correct required_history values.

Only projection needs a prior scan (required_history=2). Analysis and tracking
need none of their own: they are skipped exactly when projection, upstream of
them, is skipped.
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
