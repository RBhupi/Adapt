# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The downloader asks the queue for room before each download.

The queue holds it while the processor catches up (see
``tests/runtime/test_scan_queue.py``); a stop request while it is held ends the
batch.
"""

import pytest

from adapt.modules.acquisition.module import AwsNexradDownloader
from adapt.runtime.scan_queue import BackpressureQueue

pytestmark = pytest.mark.unit


class _RecordingQueue(BackpressureQueue):
    """Records how many downloads had happened each time room was asked for."""

    def __init__(self, gateway):
        super().__init__(10, 0.5)
        self._gateway = gateway
        self.downloads_when_asked: list[int] = []

    def wait_for_room(self, stopped, poll_seconds=0.5):
        self.downloads_when_asked.append(len(self._gateway.acquire_file_calls))
        super().wait_for_room(stopped, poll_seconds)


def test_the_downloader_asks_the_queue_for_room_before_each_download(
    make_config, fake_gateway, fake_aws_conn, fake_scan
):
    q = _RecordingQueue(fake_gateway)
    scans = [fake_scan(f"s{i}") for i in range(3)]
    d = AwsNexradDownloader(
        make_config(), result_queue=q, acquire=fake_gateway, conn=fake_aws_conn(scans)
    )

    d._process_scans(scans)

    assert q.downloads_when_asked == [0, 1, 2]
    assert len(fake_gateway.acquire_file_calls) == 3


def test_a_stop_while_the_downloader_waits_ends_the_batch_with_nothing_downloaded(
    make_config, fake_gateway, fake_aws_conn, fake_scan
):
    scans = [fake_scan(f"s{i}") for i in range(3)]
    q = BackpressureQueue(2, 0.5, sleep=lambda _seconds: d.stop())
    q.put("earlier-scan-0")
    q.put("earlier-scan-1")  # the queue is at its high level
    d = AwsNexradDownloader(
        make_config(), result_queue=q, acquire=fake_gateway, conn=fake_aws_conn(scans)
    )

    d._process_scans(scans)

    assert fake_gateway.acquire_file_calls == []
    assert q.qsize() == 2
