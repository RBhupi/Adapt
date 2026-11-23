# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The options of the ``aws_nexrad`` source."""

import pytest
from pydantic import ValidationError

from adapt.sources.aws_nexrad.config import AwsNexradOptions

pytestmark = pytest.mark.unit

_OPTIONS = (
    "latest_files",
    "latest_minutes",
    "first_check_sec",
    "expected_arrival_sec",
    "late_after_sec",
    "max_skipped_scans",
    "max_fetch_retries",
    "min_file_size",
)


def test_every_option_has_its_default():
    options = AwsNexradOptions()

    assert [getattr(options, name) for name in _OPTIONS] == [5, 60, 200, 300, 450, 3, 3, 1024]


@pytest.mark.parametrize("name", _OPTIONS)
def test_a_value_under_one_is_refused(name):
    with pytest.raises(ValidationError, match=name):
        AwsNexradOptions(**{name: 0})


@pytest.mark.parametrize(
    "thresholds",
    [
        {"first_check_sec": 300, "expected_arrival_sec": 300},
        {"expected_arrival_sec": 500, "late_after_sec": 450},
    ],
)
def test_arrival_thresholds_that_do_not_increase_are_refused(thresholds):
    with pytest.raises(
        ValidationError, match="first_check_sec < expected_arrival_sec < late_after_sec"
    ):
        AwsNexradOptions(**thresholds)


def test_the_removed_polling_interval_is_refused():
    with pytest.raises(ValidationError, match="poll_interval_sec"):
        AwsNexradOptions(poll_interval_sec=30)


def test_an_unknown_option_is_refused():
    with pytest.raises(ValidationError, match="max_queue_size"):
        AwsNexradOptions(max_queue_size=10)


def test_the_options_cannot_be_changed():
    options = AwsNexradOptions()

    with pytest.raises(ValidationError):
        options.first_check_sec = 30
