# tests/test_downloader_run.py
import pytest

from adapt.sources.aws_nexrad.config import AwsNexradOptions
from tests.helpers.offline_aws import offline

pytestmark = pytest.mark.unit


def test_run_exits_after_historical_complete(
    fake_scan, fake_aws_conn, fake_gateway, source_run, scan_queue
):
    scans = [fake_scan("one")]

    run = source_run(
        mode="historical", start_time="2024-01-01T00:00:00Z", end_time="2024-01-01T01:00:00Z"
    )

    d = offline(fake_aws_conn(scans), sleep=lambda _: None)(
        run, AwsNexradOptions(), scan_queue, fake_gateway
    )

    d.run()

    assert d.is_historical_complete()
