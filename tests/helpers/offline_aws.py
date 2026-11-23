# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The AWS downloader run offline: a fake archive connection, clock and sleep."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from adapt.downloaders.models import ArchiveScan
from adapt.sources.aws_nexrad.source import AwsNexradDownloader, Seams


def offline(conn, **seams) -> type[AwsNexradDownloader]:
    """The downloader class that reaches ``conn`` instead of the archive, and
    the ``clock`` and ``sleep`` given (the real ones otherwise)."""
    return type(
        "OfflineDownloader", (AwsNexradDownloader,), {"seams": Seams(lambda: conn, **seams)}
    )


def volume(radar: str, start: datetime, published_after_s: float = 300) -> ArchiveScan:
    """One archive volume of ``radar`` started at ``start``, published later."""
    name = f"{radar}{start:%Y%m%d_%H%M%S}_V06"
    published = start + timedelta(seconds=published_after_s)
    return ArchiveScan(f"{start:%Y/%m/%d}/{radar}/{name}", start, 2048, published)


@dataclass
class SimulatedArchive:
    """The archive as it stands at ``now``: a volume is listed once it is published.

    ``radars`` maps a UTC day to the radars with data that day (every day: ``radar``).
    Records each listing (``listed_at``) and each download time (``downloaded_at``).
    """

    now: datetime
    volumes: list[ArchiveScan]
    radar: str = "KHTX"
    radars: dict[date, list[str]] | None = None
    listed_at: list[datetime] = field(default_factory=list)
    downloaded_at: dict[str, datetime] = field(default_factory=dict)

    def clock(self) -> datetime:
        return self.now

    def _published(self) -> list[ArchiveScan]:
        return sorted(
            (v for v in self.volumes if v.published <= self.now), key=lambda v: v.scan_time
        )

    def get_avail_radars(self, y, m, d):
        day = date(int(y), int(m), int(d))
        return [self.radar] if self.radars is None else self.radars.get(day, [])

    def get_avail_scans_in_range(self, start, end, radar):
        self.listed_at.append(self.now)
        return [v for v in self._published() if start <= v.scan_time <= end]

    def get_scans_after(self, radar, key, until):
        self.listed_at.append(self.now)
        return [v for v in self._published() if v.key > key]

    def download(self, scans, target_dir, keep_aws_folders=False):
        class Done:
            def __init__(self, path):
                self.filepath = path

        done = []
        for scan in scans:
            path = target_dir / scan.key.rsplit("/", 1)[-1]
            path.write_bytes(b"x" * 2048)
            self.downloaded_at[scan.key] = self.now
            done.append(Done(path))

        class Result:
            def iter_success(self):
                return done

        return Result()


def simulate(archive, run, options, queue, gateway, *, until, processing_resumes=None):
    """Run the downloader in simulated time until ``until``.

    The processor takes every queued scan at once, from ``processing_resumes``
    on (from the start when None). Returns the downloader after ``run()`` ends.
    """

    def sleep(seconds):
        archive.now += timedelta(seconds=seconds)
        if processing_resumes is None or archive.now >= processing_resumes:
            while not queue.empty():
                queue.get_nowait()
        if archive.now >= until:
            downloader.stop()

    downloader = offline(archive, clock=archive.clock, sleep=sleep)(run, options, queue, gateway)
    downloader.run()
    return downloader
