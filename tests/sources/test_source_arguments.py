# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Every source is built and checked the same way, so the runtime builds any of
them without knowing which."""

import inspect

import pytest

from adapt.execution.source_registry import catalog_source_names, source_registry

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", catalog_source_names())
def test_a_source_takes_run_options_queue_acquire_and_nothing_else_by_position(name):
    parameters = list(inspect.signature(source_registry.get(name).__init__).parameters.values())[1:]

    assert [p.name for p in parameters[:4]] == ["run", "options", "queue", "acquire"]
    assert all(p.default is inspect.Parameter.empty for p in parameters[:4])
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters[4:])


@pytest.mark.parametrize("name", catalog_source_names())
def test_a_source_is_checked_with_the_run_and_its_options(name):
    parameters = inspect.signature(source_registry.get(name).check).parameters

    assert list(parameters) == ["run", "options"]
