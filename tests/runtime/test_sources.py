# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests for the ingress source plugin system.

Sources are registered, swappable-by-name plugins that push file paths into the
processor queue. AwsNexradDownloader (download) and LocalDirectorySource
(already-present files) both satisfy the ScanSource interface.
"""

import queue

import pytest

from adapt.configuration.schemas.internal import InternalConfig
from tests.helpers.run_configs import new_run_config

pytestmark = pytest.mark.unit


class TestSourceRegistry:
    def test_registry_resolves_builtin_sources(self):
        from adapt.modules.acquisition.module import AwsNexradDownloader
        from adapt.runtime.sources import LocalDirectorySource, source_registry

        assert source_registry.get("aws_nexrad") is AwsNexradDownloader
        assert source_registry.get("local_directory") is LocalDirectorySource

    def test_unknown_source_raises(self):
        from adapt.runtime.sources import source_registry

        with pytest.raises(KeyError, match="unknown_source"):
            source_registry.get("unknown_source")

    def test_duplicate_registration_raises(self):
        from adapt.runtime.sources import SourceRegistry

        reg = SourceRegistry()
        reg.register("dup", object)
        with pytest.raises(RuntimeError, match="dup"):
            reg.register("dup", object)

    def test_names_lists_builtin_sources(self):
        from adapt.runtime.sources import source_registry

        assert {"aws_nexrad", "local_directory"} <= set(source_registry.names())

    def test_every_registered_source_satisfies_scan_source_protocol(self):
        """The orchestrator drives sources only through the ScanSource interface."""
        from adapt.contracts.source import ScanSource
        from adapt.runtime.sources import source_registry

        for name in source_registry.names():
            cls = source_registry.get(name)
            assert issubclass(cls, ScanSource), (
                f"Source '{name}' ({cls.__name__}) does not satisfy the ScanSource "
                "protocol — the orchestrator cannot drive it. See contracts/source.py."
            )


WINDOW = {"start_time": "2024-01-01T12:00:00Z", "end_time": "2024-01-01T13:00:00Z"}


@pytest.fixture
def local(store_root):
    """Factory: the config of a historical KLOT run that reads a folder, over ``WINDOW``."""

    def _local(src_dir):
        run = {
            "source": "local_directory",
            "radar": "KLOT",
            "mode": "historical",
            **WINDOW,
            "source_dir": str(src_dir),
        }
        return new_run_config(store_root, run=run)

    return _local


def _folder(tmp_path, *names: str):
    src_dir = tmp_path / "incoming"
    src_dir.mkdir()
    for name in names:
        (src_dir / name).write_bytes(b"x" * 6000)
    return src_dir


class TestLocalDirectorySource:
    def test_queues_files_in_chronological_order(self, tmp_path, store_env, local):
        from adapt.runtime.sources import LocalDirectorySource

        # NEXRAD-style names embed the timestamp; lexical sort == chronological
        src_dir = _folder(tmp_path, "KLOT20240101_120500_V06", "KLOT20240101_120000_V06")

        cfg = local(src_dir)
        q: queue.Queue = queue.Queue()
        src = LocalDirectorySource(config=cfg, result_queue=q, acquire=store_env.acquirer)
        src.run()

        queued = []
        while not q.empty():
            queued.append(q.get())

        names = [
            store_env.collection.catalog.get_artifact(m["artifact_id"])["original_filename"]
            for m in queued
        ]
        assert names == [
            "KLOT20240101_120000_V06",
            "KLOT20240101_120500_V06",
        ]
        assert src.is_historical_complete() is True
        assert src.get_historical_progress() == (2, 2)

    def test_missing_source_dir_raises(self, tmp_path, local):
        from adapt.runtime.sources import LocalDirectorySource

        # No pipeline file gives this (it must name the folder): a hand-made config.
        cfg = InternalConfig.model_validate({**local(tmp_path).model_dump(), "source_dir": None})
        with pytest.raises(ValueError, match="source_dir"):
            LocalDirectorySource(config=cfg, result_queue=queue.Queue(), acquire=object())

    def test_only_files_in_the_run_window_are_queued(self, tmp_path, store_env, local):
        from adapt.runtime.sources import LocalDirectorySource

        src_dir = _folder(
            tmp_path,
            "KLOT20240101_115959_V06",  # before start_time
            "KLOT20240101_120000_V06",
            "KLOT20240101_130000_V06",  # end_time is inclusive
            "KLOT20240101_130001_V06",  # after end_time
        )
        q: queue.Queue = queue.Queue()
        src = LocalDirectorySource(
            config=local(src_dir), result_queue=q, acquire=store_env.acquirer
        )
        src.run()

        names = [
            store_env.collection.catalog.get_artifact(q.get()["artifact_id"])["original_filename"]
            for _ in range(q.qsize())
        ]
        assert names == ["KLOT20240101_120000_V06", "KLOT20240101_130000_V06"]
        assert src.get_historical_progress() == (2, 2)

    def test_a_hidden_file_is_ignored(self, tmp_path, store_env, local):
        from adapt.runtime.sources import LocalDirectorySource

        src_dir = _folder(tmp_path, "KLOT20240101_120000_V06", ".DS_Store")
        q: queue.Queue = queue.Queue()
        src = LocalDirectorySource(
            config=local(src_dir), result_queue=q, acquire=store_env.acquirer
        )
        src.run()

        assert src.get_historical_progress() == (1, 1)

    @pytest.mark.parametrize(
        "name",
        ["KHTX20240101_120000_V06", "notes.txt"],
        ids=["another radar", "no scan time"],
    )
    def test_a_file_not_named_for_the_radar_is_refused_when_built(self, tmp_path, local, name):
        from adapt.configuration.schemas.errors import ConfigError
        from adapt.runtime.sources import LocalDirectorySource

        src_dir = _folder(tmp_path, "KLOT20240101_120000_V06", name)

        with pytest.raises(ConfigError, match=name):
            LocalDirectorySource(
                config=local(src_dir), result_queue=queue.Queue(), acquire=object()
            )

    def test_a_folder_with_no_file_in_the_window_is_refused_when_built(self, tmp_path, local):
        from adapt.configuration.schemas.errors import ConfigError
        from adapt.runtime.sources import LocalDirectorySource

        src_dir = _folder(tmp_path, "KLOT20240101_140000_V06")

        with pytest.raises(ConfigError, match="2024-01-01T12:00:00Z"):
            LocalDirectorySource(
                config=local(src_dir), result_queue=queue.Queue(), acquire=object()
            )

    def test_a_folder_that_does_not_exist_is_refused_when_built(self, tmp_path, local):
        from adapt.configuration.schemas.errors import ConfigError
        from adapt.runtime.sources import LocalDirectorySource

        absent = tmp_path / "absent"

        with pytest.raises(ConfigError, match=str(absent)):
            LocalDirectorySource(config=local(absent), result_queue=queue.Queue(), acquire=object())

    def test_check_refuses_the_same_folders_without_building_the_source(self, tmp_path, local):
        """The cli checks the source before the run starts, through the source class."""
        from adapt.configuration.schemas.errors import ConfigError
        from adapt.runtime.sources import LocalDirectorySource

        src_dir = _folder(tmp_path, "KHTX20240101_120000_V06")

        with pytest.raises(ConfigError, match="KHTX20240101_120000_V06"):
            LocalDirectorySource.check(local(src_dir))
