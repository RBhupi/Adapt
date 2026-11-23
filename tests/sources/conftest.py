# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Shared fixtures for the source tests."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from adapt.contracts.source import SourceRun
from adapt.runtime.scan_queue import BackpressureQueue

_REALTIME = SourceRun(
    radar="TEST_RADAR", mode="realtime", start_time=None, end_time=None, source_dir=None
)


@pytest.fixture
def source_run():
    """Factory: the run values a source is handed; a realtime run of TEST_RADAR
    unless ``values`` say otherwise."""

    def _run(**values) -> SourceRun:
        return replace(_REALTIME, **values)

    return _run


@pytest.fixture
def scan_queue():
    """A queue to the processor with the run config's default levels."""
    return BackpressureQueue(100, 0.1)


# ---- AwsNexradDownloader fixtures ----
class FakeScan:
    def __init__(self, key, scan_time=None):
        self.key = key
        self.scan_time = scan_time or datetime.now(UTC)


class FakeAwsConn:
    def __init__(self, scans, radars=("TEST_RADAR", "KDIX")):
        self.scans = scans
        self.radars = list(radars)

    def get_avail_radars(self, y, m, d):
        return self.radars

    def get_avail_scans_in_range(self, start, end, radar_id):
        return self.scans

    def download(self, scans, target_dir, keep_aws_folders=False):
        class Result:
            def __init__(self, path):
                self.filepath = path

        results = []
        for scan in scans:
            path = target_dir / scan.key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * 2048)
            results.append(Result(path))

        class DownloadResults:
            def iter_success(self):
                return results

        return DownloadResults()


@pytest.fixture
def fake_scan():
    return FakeScan


@pytest.fixture
def fake_aws_conn():
    return FakeAwsConn
