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
    "poll_interval_sec",
    "max_fetch_retries",
    "min_file_size",
)


def test_every_option_has_its_default():
    options = AwsNexradOptions()

    assert [getattr(options, name) for name in _OPTIONS] == [5, 60, 300, 3, 1024]


@pytest.mark.parametrize("name", _OPTIONS)
def test_a_value_under_one_is_refused(name):
    with pytest.raises(ValidationError, match=name):
        AwsNexradOptions(**{name: 0})


def test_an_unknown_option_is_refused():
    with pytest.raises(ValidationError, match="max_queue_size"):
        AwsNexradOptions(max_queue_size=10)


def test_the_options_cannot_be_changed():
    options = AwsNexradOptions()

    with pytest.raises(ValidationError):
        options.poll_interval_sec = 30
