# tests/test_downloader_failures.py
from datetime import UTC, datetime

import pytest

from adapt.runtime.scan_queue import BackpressureQueue
from adapt.sources.aws_nexrad.config import AwsNexradOptions
from tests.helpers.offline_aws import offline

pytestmark = pytest.mark.unit


def test_download_failure_does_not_queue(fake_scan, fake_gateway, source_run):
    class FailingConn:
        def download(self, *a, **k):
            class R:
                def iter_success(self):
                    return []

            return R()

    q = BackpressureQueue(100, 0.1)
    d = offline(FailingConn())(source_run(), AwsNexradOptions(), q, fake_gateway)

    downloads = d._process_scans([fake_scan("bad", datetime.now(UTC))])

    assert downloads == []
    assert q.qsize() == 0
    assert fake_gateway.acquire_file_calls == []
