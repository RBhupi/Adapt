# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests that modules declare correct required_history values.

Only projection needs a prior scan (required_history=2). Analysis and tracking
need none of their own: they are skipped exactly when projection, upstream of
them, is skipped.
"""

import pytest

pytestmark = pytest.mark.unit


class TestNodeRequiredHistory:
    def test_ingest_node_requires_single_scan(self):
        from adapt.modules.ingest.node import LoadModule

        assert LoadModule.required_history == 1

    def test_detection_node_requires_single_scan(self):
        from adapt.modules.detection.node import DetectModule

        assert DetectModule.required_history == 1

    def test_projection_node_requires_two_scans(self):
        from adapt.modules.projection.node import ProjectionModule

        assert ProjectionModule.required_history == 2

    def test_analysis_node_needs_no_history_of_its_own(self):
        from adapt.modules.analysis.node import AnalysisModule

        assert AnalysisModule.required_history == 1

    def test_tracking_node_needs_no_history_of_its_own(self):
        from adapt.modules.tracking.node import TrackingModule

        assert TrackingModule.required_history == 1
