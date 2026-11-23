# tests/test_downloader_realtime.py
from datetime import UTC, datetime

import pytest

from adapt.runtime.scan_queue import BackpressureQueue
from adapt.sources.aws_nexrad.config import AwsNexradOptions
from tests.helpers.offline_aws import offline

pytestmark = pytest.mark.unit


def _count_downloads(conn, calls):
    """Wrap conn.download to record the scan keys of each download call."""
    original = conn.download

    def counting_download(scans, *a, **k):
        calls.append([s.key for s in scans])
        return original(scans, *a, **k)

    conn.download = counting_download


def test_realtime_download_hybrid(fake_scan, fake_aws_conn, fake_gateway, source_run):
    """Scans already in the store are acquired without downloading; new ones download."""
    now = datetime(2024, 1, 1, tzinfo=UTC)

    scans = [
        fake_scan("scan1", now),
        fake_scan("scan2", now),
    ]

    q = BackpressureQueue(100, 0.1)
    conn = fake_aws_conn(scans)
    download_calls: list = []
    _count_downloads(conn, download_calls)

    fake_gateway.acquired.add("scan1")  # already committed by a previous run

    d = offline(conn, clock=lambda: now, sleep=lambda _: None)(
        source_run(radar="KDIX"),
        AwsNexradOptions(latest_files=2, latest_minutes=30),
        q,
        fake_gateway,
    )

    downloads = d._process_scans(scans)

    assert downloads == ["scan2"]  # only the missing scan hit S3
    assert download_calls == [["scan2"]]
    assert fake_gateway.acquire_existing_calls == ["scan1"]
    assert [uri for _, uri in fake_gateway.acquire_file_calls] == ["scan2"]
    assert q.qsize() == 2  # both scans are queued for the processor
