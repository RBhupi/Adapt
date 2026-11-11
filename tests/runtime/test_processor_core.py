# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests for the RadarProcessor's own lifecycle (start, stop, queue handling).

The scientific work is one module graph; what each scan runs is covered by
``tests/execution/test_scan_graph.py`` and ``test_processor_prior_scans.py``.
"""

import queue

import pytest

from adapt.runtime.processor import RadarProcessor

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]


def _make_proc(pipeline_config, store_env):
    return RadarProcessor(
        queue.Queue(),
        pipeline_config,
        collection=store_env.collection,
        registry=store_env.registry,
        run_id=store_env.run_id,
        history=store_env.history,
    )


def test_processor_stop_sets_flag(pipeline_config, store_env):
    """stop() signals the run loop to exit."""
    proc = _make_proc(pipeline_config, store_env)
    assert not proc.stopped()
    proc.stop()
    assert proc.stopped()


def test_processor_stop_is_idempotent(pipeline_config, store_env):
    """Calling stop() twice is safe."""
    proc = _make_proc(pipeline_config, store_env)
    proc.stop()
    proc.stop()
    assert proc.stopped()


def test_processor_requires_store_wiring(pipeline_config, store_env):
    """RadarProcessor construction is keyword-only on its store dependencies."""
    with pytest.raises(TypeError):
        RadarProcessor(queue.Queue(), pipeline_config, store_env.collection)  # type: ignore[misc]
