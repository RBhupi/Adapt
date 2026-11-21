# tests/test_downloader_failures.py
from datetime import UTC, datetime

import pytest

from adapt.runtime.scan_queue import BackpressureQueue
from adapt.sources.aws_nexrad.config import AwsNexradOptions
from adapt.sources.aws_nexrad.source import AwsNexradDownloader

pytestmark = pytest.mark.unit


def test_download_failure_does_not_queue(fake_scan, fake_gateway, source_run):
    class FailingConn:
        def get_avail_scans_in_range(self, *a):
            return [fake_scan("bad", datetime.now(UTC))]

        def download(self, *a, **k):
            class R:
                def iter_success(self):
                    return []

            return R()

    q = BackpressureQueue(100, 0.1)
    d = AwsNexradDownloader(source_run(), AwsNexradOptions(), q, fake_gateway, conn=FailingConn())

    downloads = d._download_realtime()

    assert downloads == []
    assert q.qsize() == 0
    assert fake_gateway.acquire_file_calls == []


def test_fetch_scans_retries_then_returns_empty(fake_gateway, source_run, scan_queue):
    """Persistent fetch failure is retried max_fetch_retries times, then [] (no crash)."""
    calls = {"fetch": 0}

    class ExplodingConn:
        def get_avail_scans_in_range(self, *a):
            calls["fetch"] += 1
            raise RuntimeError("AWS down")

    sleeps: list = []
    d = AwsNexradDownloader(
        source_run(),
        AwsNexradOptions(max_fetch_retries=4),
        scan_queue,
        fake_gateway,
        conn=ExplodingConn(),
        sleeper=sleeps.append,
    )

    scans = d._fetch_scans(datetime.now(UTC), datetime.now(UTC))

    assert scans == []
    assert calls["fetch"] == 4  # one attempt per retry
    assert sleeps == [1, 2, 3]  # backoff between attempts, none after the last


def test_fetch_scans_recovers_on_retry(fake_scan, fake_gateway, source_run, scan_queue):
    """A transient failure followed by success returns the scans."""
    calls = {"fetch": 0}
    good = [fake_scan("KLOT20250305_120000", datetime.now(UTC))]

    class FlakyConn:
        def get_avail_scans_in_range(self, *a):
            calls["fetch"] += 1
            if calls["fetch"] < 3:
                raise RuntimeError("transient AWS error")
            return good

    d = AwsNexradDownloader(
        source_run(),
        AwsNexradOptions(max_fetch_retries=3),
        scan_queue,
        fake_gateway,
        conn=FlakyConn(),
        sleeper=lambda _: None,
    )

    scans = d._fetch_scans(datetime.now(UTC), datetime.now(UTC))

    assert scans == good
    assert calls["fetch"] == 3
