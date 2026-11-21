# tests/test_downloader_init.py
from datetime import UTC, datetime, timedelta

import pytest

from adapt.sources.aws_nexrad.config import AwsNexradOptions
from adapt.sources.aws_nexrad.source import AwsNexradDownloader

pytestmark = pytest.mark.unit


class _AskedConn:
    """Records what the archive is asked for; it lists ``scans``."""

    def __init__(self, scans, fake_aws_conn):
        self._conn = fake_aws_conn(scans)
        self.asked: list[tuple[datetime, datetime, str]] = []

    def get_avail_scans_in_range(self, start, end, radar_id):
        self.asked.append((start, end, radar_id))
        return self._conn.get_avail_scans_in_range(start, end, radar_id)

    def get_avail_radars(self, *date):
        return []

    def download(self, scans, target_dir, keep_aws_folders=False):
        return self._conn.download(scans, target_dir, keep_aws_folders)


def test_a_realtime_poll_asks_for_the_runs_radar_and_keeps_the_newest_scans(
    source_run, scan_queue, fake_scan, fake_aws_conn, fake_gateway
):
    """The run's radar, and the options latest_minutes and latest_files, shape a poll."""
    now = datetime(2024, 1, 1, 12, tzinfo=UTC)
    scans = [fake_scan(f"s{i}", now - timedelta(minutes=5 - i)) for i in range(5)]
    conn = _AskedConn(scans, fake_aws_conn)
    d = AwsNexradDownloader(
        source_run(radar="KDIX"),
        AwsNexradOptions(latest_files=2, latest_minutes=30),
        scan_queue,
        fake_gateway,
        conn=conn,
        clock=lambda: now,
    )

    d._download_realtime()

    assert conn.asked == [(now - timedelta(minutes=30), now, "KDIX")]
    assert [uri for _, uri in fake_gateway.acquire_file_calls] == ["s3", "s4"]


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


def test_a_historical_run_asks_for_its_window_and_a_realtime_run_for_the_last_minutes(
    source_run, scan_queue, fake_aws_conn, fake_gateway
):
    """The downloader's mode is the run's."""
    now = datetime(2024, 6, 1, tzinfo=UTC)
    historical = _AskedConn([], fake_aws_conn)
    realtime = _AskedConn([], fake_aws_conn)
    for run, conn in ((source_run(**_WINDOW), historical), (source_run(), realtime)):
        AwsNexradDownloader(
            run, AwsNexradOptions(), scan_queue, fake_gateway, conn=conn, clock=lambda: now
        )._download_task()

    assert historical.asked == [
        (datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, 1, tzinfo=UTC), "TEST_RADAR")
    ]
    assert realtime.asked == [(now - timedelta(minutes=60), now, "TEST_RADAR")]


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

    d = AwsNexradDownloader(
        source_run(), AwsNexradOptions(), scan_queue, fake_gateway, conn=TinyFileConn()
    )

    assert d._download_scan(fake_scan("tiny")) is None
