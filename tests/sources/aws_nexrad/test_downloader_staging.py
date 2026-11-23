# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The downloader's staging folder lives only as long as the downloader runs."""

import tempfile

import pytest

from adapt.runtime.scan_queue import BackpressureQueue
from adapt.sources.aws_nexrad.config import AwsNexradOptions
from tests.helpers.offline_aws import offline

pytestmark = pytest.mark.unit


def test_the_staging_folder_is_removed_when_the_downloader_stops(
    fake_aws_conn, fake_gateway, source_run, tmp_path, monkeypatch
):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    downloader = offline(fake_aws_conn([]), sleep=lambda _: None)(
        source_run(radar="KDIX"), AwsNexradOptions(), BackpressureQueue(100, 0.1), fake_gateway
    )
    assert list(tmp_path.glob("adapt_download_*"))  # made when the downloader is built

    downloader.stop()
    downloader.run()

    assert not list(tmp_path.glob("adapt_download_*"))
