# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests for the source selector config: which ingress source feeds the pipeline.

`source` names a registered source plugin, as the pipeline file's `run.source`
gives it; `source_dir` is the directory a local source scans.
"""

import pytest

pytestmark = pytest.mark.unit


class TestSourceSelector:
    def test_a_downloading_run_has_no_source_folder(self, make_config):
        cfg = make_config(run={"source": "aws_nexrad"})
        assert cfg.source == "aws_nexrad"
        assert cfg.source_dir is None

    def test_user_can_select_local_directory_source(self, make_config):
        run = {
            "source": "local_directory",
            "source_dir": "/data/in",
            "mode": "historical",
            "start_time": "2024-01-01T12:00:00Z",
            "end_time": "2024-01-01T13:00:00Z",
        }
        cfg = make_config(run=run)
        assert cfg.source == "local_directory"
        assert cfg.source_dir == "/data/in"
