# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests that the orchestrator resolves its ingress source by name via the registry."""

import pytest

from adapt.runtime.orchestrator import PipelineOrchestrator

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]


class TestOrchestratorSourceResolution:
    def test_default_source_is_aws_nexrad(self, pipeline_config):
        from adapt.sources.aws_nexrad.source import AwsNexradDownloader

        orch = PipelineOrchestrator(pipeline_config)
        orch.collection = orch.store.collection("TEST_RADAR")
        source = orch._create_source()
        assert isinstance(source, AwsNexradDownloader)
        orch.store.close()

    def test_local_directory_source_selected_by_config(self, pipeline_config, tmp_path):
        from adapt.sources.local_directory.source import LocalDirectorySource

        src_dir = tmp_path / "in"
        src_dir.mkdir()
        (src_dir / "TEST_RADAR20240518_120000_V06").write_bytes(b"x" * 6000)
        cfg = pipeline_config.model_copy(
            update={
                "source": "local_directory",
                "source_dir": str(src_dir),
                "downloader": pipeline_config.downloader.model_copy(
                    update={
                        "start_time": "2024-05-18T11:00:00Z",
                        "end_time": "2024-05-18T13:00:00Z",
                    }
                ),
            }
        )
        orch = PipelineOrchestrator(cfg)
        orch.collection = orch.store.collection("TEST_RADAR")
        source = orch._create_source()
        assert isinstance(source, LocalDirectorySource)
        orch.store.close()
