# tests/test_downloader_failures.py
from datetime import UTC, datetime

import pytest

from adapt.modules.acquisition.module import AwsNexradDownloader

pytestmark = pytest.mark.unit


def test_download_failure_does_not_queue(tmp_path, fake_scan, make_config):
    class FailingConn:
        def get_avail_scans_in_range(self, *a):
            return [fake_scan("bad", datetime.now(UTC))]

        def download(self, *a, **k):
            class R:
                def iter_success(self):
                    return []

            return R()

    config = make_config()
    d = AwsNexradDownloader(config, output_dir=tmp_path, conn=FailingConn())

    downloads = d._download_realtime()
    assert downloads == []


def test_fetch_scans_exception_returns_empty(tmp_path, make_config):
    class ExplodingConn:
        def get_avail_scans_in_range(self, *a):
            raise RuntimeError("AWS down")

    config = make_config()
    d = AwsNexradDownloader(config, output_dir=tmp_path, conn=ExplodingConn())

    scans = d._fetch_scans(datetime.now(UTC), datetime.now(UTC))
    assert scans == []
