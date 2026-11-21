# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The local-directory source: the scans of a folder within the run window, in time order."""

import queue

import pytest

from adapt.sources.local_directory.config import LocalDirectoryOptions

pytestmark = pytest.mark.unit


WINDOW = {"start_time": "2024-01-01T12:00:00Z", "end_time": "2024-01-01T13:00:00Z"}


@pytest.fixture
def local(source_run):
    """Factory: the values of a historical KLOT run that reads a folder, over ``WINDOW``."""

    def _local(src_dir):
        return source_run(radar="KLOT", mode="historical", **WINDOW, source_dir=str(src_dir))

    return _local


def _folder(tmp_path, *names: str):
    src_dir = tmp_path / "incoming"
    src_dir.mkdir()
    for name in names:
        (src_dir / name).write_bytes(b"x" * 6000)
    return src_dir


class TestLocalDirectorySource:
    def test_queues_files_in_chronological_order(self, tmp_path, store_env, local):
        from adapt.sources.local_directory.source import LocalDirectorySource

        # NEXRAD-style names embed the timestamp; lexical sort == chronological
        src_dir = _folder(tmp_path, "KLOT20240101_120500_V06", "KLOT20240101_120000_V06")

        q: queue.Queue = queue.Queue()
        src = LocalDirectorySource(local(src_dir), LocalDirectoryOptions(), q, store_env.acquirer)
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
        from adapt.sources.local_directory.source import LocalDirectorySource

        # No pipeline file gives this (it must name the folder): hand-made run values.
        run = local(tmp_path)
        absent = type(run)(**{**vars(run), "source_dir": None})
        with pytest.raises(ValueError, match="source_dir"):
            LocalDirectorySource(absent, LocalDirectoryOptions(), queue.Queue(), object())

    def test_only_files_in_the_run_window_are_queued(self, tmp_path, store_env, local):
        from adapt.sources.local_directory.source import LocalDirectorySource

        src_dir = _folder(
            tmp_path,
            "KLOT20240101_115959_V06",  # before start_time
            "KLOT20240101_120000_V06",
            "KLOT20240101_130000_V06",  # end_time is inclusive
            "KLOT20240101_130001_V06",  # after end_time
        )
        q: queue.Queue = queue.Queue()
        src = LocalDirectorySource(local(src_dir), LocalDirectoryOptions(), q, store_env.acquirer)
        src.run()

        names = [
            store_env.collection.catalog.get_artifact(q.get()["artifact_id"])["original_filename"]
            for _ in range(q.qsize())
        ]
        assert names == ["KLOT20240101_120000_V06", "KLOT20240101_130000_V06"]
        assert src.get_historical_progress() == (2, 2)

    def test_a_hidden_file_is_ignored(self, tmp_path, store_env, local):
        from adapt.sources.local_directory.source import LocalDirectorySource

        src_dir = _folder(tmp_path, "KLOT20240101_120000_V06", ".DS_Store")
        q: queue.Queue = queue.Queue()
        src = LocalDirectorySource(local(src_dir), LocalDirectoryOptions(), q, store_env.acquirer)
        src.run()

        assert src.get_historical_progress() == (1, 1)

    @pytest.mark.parametrize(
        "name",
        ["KHTX20240101_120000_V06", "notes.txt"],
        ids=["another radar", "no scan time"],
    )
    def test_a_file_not_named_for_the_radar_is_refused_when_built(self, tmp_path, local, name):
        from adapt.sources.local_directory.source import LocalDirectorySource

        src_dir = _folder(tmp_path, "KLOT20240101_120000_V06", name)

        with pytest.raises(ValueError, match=name):
            LocalDirectorySource(local(src_dir), LocalDirectoryOptions(), queue.Queue(), object())

    def test_a_folder_with_no_file_in_the_window_is_refused_when_built(self, tmp_path, local):
        from adapt.sources.local_directory.source import LocalDirectorySource

        src_dir = _folder(tmp_path, "KLOT20240101_140000_V06")

        with pytest.raises(ValueError, match="2024-01-01T12:00:00Z"):
            LocalDirectorySource(local(src_dir), LocalDirectoryOptions(), queue.Queue(), object())

    def test_a_folder_that_does_not_exist_is_refused_when_built(self, tmp_path, local):
        from adapt.sources.local_directory.source import LocalDirectorySource

        absent = tmp_path / "absent"

        with pytest.raises(ValueError, match=str(absent)):
            LocalDirectorySource(local(absent), LocalDirectoryOptions(), queue.Queue(), object())

    def test_check_refuses_the_same_folders_without_building_the_source(self, tmp_path, local):
        """The cli checks the source before the run starts, through the source class."""
        from adapt.sources.local_directory.source import LocalDirectorySource

        src_dir = _folder(tmp_path, "KHTX20240101_120000_V06")

        with pytest.raises(ValueError, match="KHTX20240101_120000_V06"):
            LocalDirectorySource.check(local(src_dir), LocalDirectoryOptions())
