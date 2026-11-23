# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""When the realtime source next looks in the archive.

A fixed schedule by the age of the last volume published: nothing until
``first_check_sec``, then every 20 s, every 10 s from ``expected_arrival_sec``
and every 30 s once the volume is late (``late_after_sec``) or none is known.
"""

from datetime import datetime, timedelta

from adapt.sources.aws_nexrad.config import AwsNexradOptions

__all__ = ["next_check_at"]

_EARLY_STEP = timedelta(seconds=20)
_EXPECTED_STEP = timedelta(seconds=10)
_LATE_STEP = timedelta(seconds=30)


def next_check_at(
    last_published: datetime | None, now: datetime, options: AwsNexradOptions
) -> datetime:
    """The time of the next listing, from the last volume's S3 publication time."""
    if last_published is None:
        return now + _LATE_STEP
    age = (now - last_published).total_seconds()
    if age < options.first_check_sec:
        return last_published + timedelta(seconds=options.first_check_sec)
    if age < options.expected_arrival_sec:
        return now + _EARLY_STEP
    if age < options.late_after_sec:
        return now + _EXPECTED_STEP
    return now + _LATE_STEP
