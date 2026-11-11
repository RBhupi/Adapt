# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests for the RadarProcessor's rolling history and enabled-module list."""

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


class TestProcessorHistoryAndModules:
    def test_processor_has_empty_scan_history_on_init(self, pipeline_config, store_env):
        """Processor starts with empty scan history."""
        proc = _make_proc(pipeline_config, store_env)
        assert proc._scan_history == []

    def test_extended_module_is_enabled_by_default(self, pipeline_config, store_env):
        """cell_volume_stats is a default extended module, listed after the core ones."""
        proc = _make_proc(pipeline_config, store_env)
        assert proc.module_names[-1] == "cell_volume_stats"

    def test_a_run_without_extended_modules_lists_only_core(self, pipeline_config, store_env):
        core = ["ingest", "detection", "projection", "analysis", "tracking"]
        cfg = pipeline_config.model_copy(update={"modules": core})
        proc = _make_proc(cfg, store_env)
        assert set(proc.module_names) == set(core)
