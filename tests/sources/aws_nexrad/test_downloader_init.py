# tests/test_downloader_init.py
import pytest

from adapt.sources.aws_nexrad.config import AwsNexradOptions
from adapt.sources.aws_nexrad.source import AwsNexradDownloader
from tests.helpers.offline_aws import offline

pytestmark = pytest.mark.unit


def test_stop_sets_event(source_run, scan_queue, fake_gateway):
    """Stop event prevents downloader from polling."""
    d = AwsNexradDownloader(source_run(), AwsNexradOptions(), scan_queue, fake_gateway)
    assert not d.stopped()
    d.stop()
    assert d.stopped()


_WINDOW = {
    "mode": "historical",
    "start_time": "2024-01-01T00:00:00Z",
    "end_time": "2024-01-01T01:00:00Z",
}


def test_parse_time_range(source_run, scan_queue, fake_gateway):
    """Downloader parses time range correctly."""
    d = AwsNexradDownloader(source_run(**_WINDOW), AwsNexradOptions(), scan_queue, fake_gateway)

    start, end = d._parse_time_range()

    assert start.tzinfo is not None
    assert end > start
    assert (end - start).total_seconds() == 3600


def test_a_historical_run_without_its_window_is_refused_when_it_polls(
    source_run, scan_queue, fake_gateway
):
    d = AwsNexradDownloader(
        source_run(mode="historical"), AwsNexradOptions(), scan_queue, fake_gateway
    )

    with pytest.raises(ValueError, match="run.start_time and run.end_time"):
        d._parse_time_range()


def test_download_scan_rejects_small_files(fake_scan, source_run, scan_queue, fake_gateway):
    """Downloaded files below minimum size are discarded (None returned)."""

    class TinyFileConn:
        def download(self, scans, target_dir, keep_aws_folders=False):
            class Result:
                def __init__(self, path):
                    self.filepath = path

            results = []
            for scan in scans:
                path = target_dir / scan.key
                path.write_bytes(b"x")  # below min_file_size
                results.append(Result(path))

            class DownloadResults:
                def iter_success(self):
                    return results

            return DownloadResults()

    d = offline(TinyFileConn())(source_run(), AwsNexradOptions(), scan_queue, fake_gateway)

    assert d._download_scan(fake_scan("tiny")) is None
