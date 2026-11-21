from datetime import UTC, datetime, timedelta

import pytest

from adapt.sources.aws_nexrad.config import AwsNexradOptions
from adapt.sources.aws_nexrad.source import AwsNexradDownloader


@pytest.mark.integration
def test_real_aws_listing(source_run, scan_queue, fake_gateway):
    """Test real AWS NEXRAD data listing.

    Uses a known radar ID (KMOB) to ensure we get real data from AWS.
    Skips if no scans are available (expected during low-activity periods).
    """
    run = source_run(radar="KHTX")  # Use a known radar with consistent data
    d = AwsNexradDownloader(run, AwsNexradOptions(), scan_queue, fake_gateway)
    end = datetime.now(UTC)
    start = end - timedelta(minutes=60)
    scans = d._fetch_scans(start, end)

    # Skip if no scans available (integration test depends on real AWS data availability)
    if not scans:
        pytest.skip(
            "No NEXRAD scans available in AWS for the past 60 minutes "
            "(expected during low-activity periods)"
        )
