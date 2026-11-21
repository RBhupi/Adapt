# tests/test_downloader_queue.py

import pytest

from adapt.runtime.scan_queue import BackpressureQueue
from adapt.sources.aws_nexrad.source import AwsNexradDownloader

pytestmark = pytest.mark.unit


def test_process_scans_queues_gateway_message(fake_scan, fake_aws_conn, fake_gateway, make_config):
    """Each new scan puts the gateway's acquired-scan message on the queue."""
    q = BackpressureQueue(100, 0.1)
    scan = fake_scan("scan1")
    d = AwsNexradDownloader(
        make_config(),
        result_queue=q,
        acquire=fake_gateway,
        conn=fake_aws_conn([scan]),
    )

    d._process_scans([scan])

    item = q.get_nowait()
    assert item == fake_gateway.messages[0]
    assert item["artifact_id"] == "art-1"
    assert item["scan_id"] == "sid-scan1"
    assert item["scan_time"] == scan.scan_time
    assert "queued_at" in item


def test_process_scans_does_not_requeue_known_uri(
    fake_scan, fake_aws_conn, fake_gateway, make_config
):
    """A source URI already queued this run is skipped on later passes."""
    q = BackpressureQueue(100, 0.1)
    scan = fake_scan("scan1")
    d = AwsNexradDownloader(
        make_config(),
        result_queue=q,
        acquire=fake_gateway,
        conn=fake_aws_conn([scan]),
    )

    d._process_scans([scan])
    d._process_scans([scan])

    assert q.qsize() == 1
    assert [uri for _, uri in fake_gateway.acquire_file_calls] == ["scan1"]
