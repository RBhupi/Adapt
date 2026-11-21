# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""A module reads stored rows of the run only through what it declares."""

import pytest

from adapt.modules.base import BaseModule

pytestmark = pytest.mark.unit


def test_a_module_reads_nothing_from_the_store_unless_it_declares_it():
    class _Plain(BaseModule):
        name = "plain"
        inputs = ["scan_time"]
        outputs = ["plain_out"]

        def run(self, context: dict) -> dict:
            return {}

    assert _Plain.store_reads == ()
    assert _Plain().store_reads == ()
