# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""When the source next looks for a volume: by the age of the last one published."""

from datetime import UTC, datetime, timedelta

import pytest

from adapt.sources.aws_nexrad.config import AwsNexradOptions
from adapt.sources.aws_nexrad.schedule import next_check_at

pytestmark = pytest.mark.unit

_PUBLISHED = datetime(2026, 11, 23, 8, 0, 0, tzinfo=UTC)
_OPTIONS = AwsNexradOptions(first_check_sec=200, expected_arrival_sec=300, late_after_sec=450)


def _after(seconds):
    return _PUBLISHED + timedelta(seconds=seconds)


def test_before_the_first_check_it_waits_until_the_first_check():
    assert next_check_at(_PUBLISHED, _after(5), _OPTIONS) == _after(200)


@pytest.mark.parametrize(
    ("age", "step"),
    [(200, 20), (299, 20), (300, 10), (449, 10), (450, 30), (900, 30)],
)
def test_after_the_first_check_it_looks_more_often_until_the_volume_is_late(age, step):
    assert next_check_at(_PUBLISHED, _after(age), _OPTIONS) == _after(age + step)


def test_with_no_volume_yet_it_looks_at_the_late_step():
    now = _after(0)

    assert next_check_at(None, now, _OPTIONS) == now + timedelta(seconds=30)
