# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The options of the ``local_directory`` source."""

import pytest
from pydantic import ValidationError

from adapt.sources.local_directory.config import LocalDirectoryOptions

pytestmark = pytest.mark.unit


def test_the_minimum_file_size_is_1024_bytes_by_default():
    assert LocalDirectoryOptions().min_file_size == 1024


def test_a_minimum_file_size_under_one_is_refused():
    with pytest.raises(ValidationError, match="min_file_size"):
        LocalDirectoryOptions(min_file_size=0)


def test_an_unknown_option_is_refused():
    with pytest.raises(ValidationError, match="poll_interval_sec"):
        LocalDirectoryOptions(poll_interval_sec=30)


def test_the_options_cannot_be_changed():
    options = LocalDirectoryOptions()

    with pytest.raises(ValidationError):
        options.min_file_size = 1
