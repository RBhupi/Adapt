# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""A historical run lists its window one UTC day at a time."""

from datetime import UTC, datetime, timedelta

import pytest

from adapt.sources.aws_nexrad.config import AwsNexradOptions
from tests.helpers.offline_aws import SimulatedArchive, offline, volume

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 11, 23, 12, tzinfo=UTC)
_WINDOW = {
    "mode": "historical",
    "start_time": "2026-11-20T23:00:00Z",
    "end_time": "2026-11-21T01:00:00Z",
}


class _OrderedArchive(SimulatedArchive):
    """Records listings and downloads in the order they happen."""

    def __init__(self, volumes):
        super().__init__(now=_NOW, volumes=volumes)
        self.events: list[tuple[str, object]] = []

    def get_avail_scans_in_range(self, start, end, radar):
        self.events.append(("list", start.date()))
        return super().get_avail_scans_in_range(start, end, radar)

    def download(self, scans, target_dir, keep_aws_folders=False):
        self.events.extend(("download", s.scan_time.date()) for s in scans)
        return super().download(scans, target_dir, keep_aws_folders)


def _run(archive, run, scan_queue, fake_gateway, sleeps=None):
    sleep = (lambda _: None) if sleeps is None else sleeps.append
    d = offline(archive, clock=archive.clock, sleep=sleep)(
        run, AwsNexradOptions(max_fetch_retries=4), scan_queue, fake_gateway
    )
    d.run()
    return d


def test_each_day_is_queued_before_the_next_day_is_listed(source_run, scan_queue, fake_gateway):
    first = datetime(2026, 11, 20, 23, 10, tzinfo=UTC)
    archive = _OrderedArchive([volume("KHTX", first + timedelta(minutes=30 * i)) for i in range(4)])

    d = _run(archive, source_run(radar="KHTX", **_WINDOW), scan_queue, fake_gateway)

    day1, day2 = first.date(), first.date() + timedelta(days=1)
    assert archive.events == [
        ("list", day1),
        ("download", day1),
        ("download", day1),
        ("list", day2),
        ("download", day2),
        ("download", day2),
    ]
    assert d.is_historical_complete()
    assert d.get_historical_progress() == (4, 4)


def test_a_window_without_volumes_fails_the_run(source_run, scan_queue, fake_gateway):
    with pytest.raises(ValueError, match="no volumes of KHXT .* check the radar name"):
        _run(_OrderedArchive([]), source_run(radar="KHXT", **_WINDOW), scan_queue, fake_gateway)


def test_a_listing_that_keeps_failing_fails_the_run_after_its_retries(
    source_run, scan_queue, fake_gateway
):
    class Down(_OrderedArchive):
        def get_avail_scans_in_range(self, start, end, radar):
            self.events.append(("list", start.date()))
            raise RuntimeError("AWS down")

    archive, sleeps = Down([]), []

    with pytest.raises(RuntimeError, match="AWS down"):
        _run(archive, source_run(radar="KHTX", **_WINDOW), scan_queue, fake_gateway, sleeps)

    assert len(archive.events) == 4
    assert sleeps == [1, 2, 3]


def test_a_listing_that_fails_once_is_retried(source_run, scan_queue, fake_gateway):
    class Flaky(_OrderedArchive):
        failed = False

        def get_avail_scans_in_range(self, start, end, radar):
            if not self.failed:
                self.failed = True
                raise RuntimeError("transient AWS error")
            return super().get_avail_scans_in_range(start, end, radar)

    archive = Flaky([volume("KHTX", datetime(2026, 11, 20, 23, 10, tzinfo=UTC))])

    d = _run(archive, source_run(radar="KHTX", **_WINDOW), scan_queue, fake_gateway)

    assert d.get_historical_progress() == (1, 1)
