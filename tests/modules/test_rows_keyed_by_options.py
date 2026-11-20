# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``rows_keyed_by_options``: which modules keep the rows of different options apart.

A module that declares it may run over a stored run with options other than
the run recorded for it, so the declaration must be true of every table it
writes: the key of each holds the hash of the module's options.
"""

import pytest

from adapt.contracts import ProductTableWrite
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import catalog_module_names

pytestmark = pytest.mark.unit


def _keyed() -> list[str]:
    return sorted(n for n in catalog_module_names() if registry.get(n).rows_keyed_by_options)


def test_the_modules_that_key_their_rows_by_their_options():
    assert _keyed() == ["arm_radar_comm", "target_selection"]


def test_every_table_of_such_a_module_has_the_options_hash_in_its_key():
    for name in _keyed():
        stored = registry.get(name).persistence

        assert stored, name
        for spec in stored:
            assert isinstance(spec, ProductTableWrite), (name, spec)
            assert "config_hash" in spec.primary_key, (name, spec.table)
