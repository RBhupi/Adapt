# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests that the orchestrator resolves its ingress source by name via the registry."""

import pytest

from adapt.execution.source_registry import source_registry
from adapt.runtime.orchestrator import PipelineOrchestrator

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]


class _StandInSource:
    """A source registered for one test; it remembers what it was built with."""

    source_name = "stand_in"
    summary = "a source of no scans"

    def __init__(self, config, result_queue, acquire) -> None:
        self.built_with = (config, result_queue, acquire)

    @classmethod
    def check(cls, config) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        pass

    def is_historical_complete(self) -> bool:
        return True

    def get_historical_progress(self) -> tuple[int, int]:
        return 0, 0


@pytest.fixture
def stand_in_source():
    source_registry.register(_StandInSource)
    yield _StandInSource
    source_registry.unregister(_StandInSource.source_name)


class TestOrchestratorSourceResolution:
    def test_the_source_the_run_names_is_built_from_the_registry(
        self, pipeline_config, stand_in_source
    ):
        cfg = pipeline_config.model_copy(update={"source": "stand_in"})
        orch = PipelineOrchestrator(cfg)
        orch.collection = orch.store.collection("TEST_RADAR")

        source = orch._create_source()

        assert isinstance(source, stand_in_source)
        config, result_queue, acquire = source.built_with
        assert config is cfg
        assert result_queue is orch.downloader_queue
        assert acquire is not None
        orch.store.close()

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
