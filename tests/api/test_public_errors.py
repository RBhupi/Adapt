# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Consumers catch StoreClient failures through the public API alone."""

import pytest

from adapt.api import StoreClient, StoreError

pytestmark = pytest.mark.unit


def test_store_failure_is_catchable_as_public_store_error(tmp_path):
    with pytest.raises(StoreError, match="adapt init"):
        StoreClient(tmp_path)
