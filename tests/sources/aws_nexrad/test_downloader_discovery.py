# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Realtime discovery against a simulated archive in simulated time."""

import logging
from datetime import UTC, datetime, timedelta

import pytest

from adapt.sources.aws_nexrad.config import AwsNexradOptions
from tests.helpers.offline_aws import SimulatedArchive, simulate, volume

pytestmark = pytest.mark.unit

_START = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)


def _every(seconds, first, count):
    """``count`` KHTX volumes started ``seconds`` apart from ``first``."""
    return [volume("KHTX", first + timedelta(seconds=seconds * i)) for i in range(count)]


def _after_start(seconds):
    return _START + timedelta(seconds=seconds)


def test_a_misspelled_radar_fails_the_start(source_run, scan_queue, fake_gateway):
    archive = SimulatedArchive(now=_START, volumes=[])

    with pytest.raises(ValueError, match="'KHXT' has no data .* check the radar name"):
        simulate(
            archive,
            source_run(radar="KHXT"),
            AwsNexradOptions(),
            scan_queue,
            fake_gateway,
            until=_after_start(60),
        )


def test_a_radar_with_data_only_yesterday_starts(source_run, scan_queue, fake_gateway):
    yesterday = (_START - timedelta(days=1)).date()
    archive = SimulatedArchive(now=_START, volumes=[], radars={yesterday: ["KHTX"]})

    simulate(
        archive,
        source_run(radar="KHTX"),
        AwsNexradOptions(),
        scan_queue,
        fake_gateway,
        until=_after_start(60),
    )


def test_the_start_takes_the_newest_latest_files_of_the_window(
    source_run, scan_queue, fake_gateway, caplog
):
    before = _every(300, _START - timedelta(minutes=50), 9)  # published up to _START
    archive = SimulatedArchive(now=_START, volumes=before)

    with caplog.at_level(logging.WARNING):
        simulate(
            archive,
            source_run(radar="KHTX"),
            AwsNexradOptions(latest_files=2),
            scan_queue,
            fake_gateway,
            until=_after_start(10),
        )

    assert [uri for _, uri in fake_gateway.acquire_file_calls] == [v.key for v in before[-2:]]
    assert "Skipped" not in caplog.text


def test_each_new_volume_is_downloaded_within_20_s_of_its_publication(
    source_run, scan_queue, fake_gateway
):
    vols = _every(300, _START - timedelta(seconds=280), 12)
    archive = SimulatedArchive(now=_START, volumes=vols)

    simulate(
        archive,
        source_run(radar="KHTX"),
        AwsNexradOptions(),
        scan_queue,
        fake_gateway,
        until=_after_start(3600),
    )

    delays = [(archive.downloaded_at[v.key] - v.published).total_seconds() for v in vols]
    assert len(delays) == 12
    assert max(delays) <= 20


def test_it_does_not_look_before_first_check_sec_after_a_volume(
    source_run, scan_queue, fake_gateway
):
    vols = _every(300, _START - timedelta(seconds=280), 12)
    archive = SimulatedArchive(now=_START, volumes=vols)

    simulate(
        archive,
        source_run(radar="KHTX"),
        AwsNexradOptions(first_check_sec=200),
        scan_queue,
        fake_gateway,
        until=_after_start(3600),
    )

    published = [v.published for v in vols]
    for before, listed in zip(archive.listed_at, archive.listed_at[1:], strict=False):
        known = [p for p in published if p <= before]  # what the earlier listing returned
        if known:
            assert (listed - max(known)).total_seconds() >= 200


def test_while_processing_is_behind_only_the_newest_volume_is_downloaded(
    source_run, scan_queue, fake_gateway, caplog
):
    vols = _every(300, _START - timedelta(seconds=300), 5)  # vols[0] seeds the run
    archive = SimulatedArchive(now=_START, volumes=vols)

    with caplog.at_level(logging.WARNING):
        simulate(
            archive,
            source_run(radar="KHTX"),
            AwsNexradOptions(latest_files=1),
            scan_queue,
            fake_gateway,
            until=_after_start(1300),
            processing_resumes=_after_start(700),
        )

    downloaded = [uri for _, uri in fake_gateway.acquire_file_calls]
    assert vols[1].key not in downloaded
    assert downloaded == [vols[0].key, vols[2].key, vols[3].key, vols[4].key]
    assert "Skipped 1 volume (KHTX20261123_080000_V06)" in caplog.text


def test_too_many_skipped_volumes_stop_the_run(source_run, scan_queue, fake_gateway):
    vols = _every(300, _START - timedelta(seconds=300), 8)
    archive = SimulatedArchive(now=_START, volumes=vols)

    with pytest.raises(RuntimeError, match="skipped 3 volumes of KHTX"):
        simulate(
            archive,
            source_run(radar="KHTX"),
            AwsNexradOptions(latest_files=1, max_skipped_scans=3),
            scan_queue,
            fake_gateway,
            until=_after_start(3600),
            processing_resumes=_after_start(10_000),
        )
