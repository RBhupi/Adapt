# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests for RadarProcessor graph-based processing.

The processor delegates scientific work to two GraphExecutors built at startup:
_single_executor (ingest + detection) and _multi_executor (projection + analysis + tracking).
These tests verify the orchestration layer: initialization, stop/start lifecycle.
"""

import queue

import pandas as pd
import pytest

from adapt.execution.graph.executor import GraphExecutor
from adapt.runtime.processor import RadarProcessor

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]


def _make_proc(pipeline_config, pipeline_output_dirs, test_repository):
    return RadarProcessor(
        queue.Queue(), pipeline_config, pipeline_output_dirs,
        repository=test_repository,
    )


def test_processor_initializes_with_two_executors(
    pipeline_config, pipeline_output_dirs, test_repository
):
    """Processor creates two GraphExecutors (single-frame and multi-frame) on init."""
    proc = _make_proc(pipeline_config, pipeline_output_dirs, test_repository)
    assert isinstance(proc._single_executor, GraphExecutor)
    assert isinstance(proc._multi_executor, GraphExecutor)


def test_single_executor_contains_ingest_and_detection(
    pipeline_config, pipeline_output_dirs, test_repository
):
    """_single_executor graph covers exactly the ingest and detection nodes."""
    proc = _make_proc(pipeline_config, pipeline_output_dirs, test_repository)
    single_names = {n.name for n in proc._single_executor.nodes}
    assert "ingest" in single_names
    assert "detection" in single_names


def test_multi_executor_contains_projection_analysis_tracking(
    pipeline_config, pipeline_output_dirs, test_repository
):
    """_multi_executor graph covers projection, analysis, and tracking nodes."""
    proc = _make_proc(pipeline_config, pipeline_output_dirs, test_repository)
    multi_names = {n.name for n in proc._multi_executor.nodes}
    assert "projection" in multi_names
    assert "analysis" in multi_names
    assert "tracking" in multi_names


def test_processor_stop_sets_flag(
    pipeline_config, pipeline_output_dirs, test_repository
):
    """stop() signals the run loop to exit."""
    proc = _make_proc(pipeline_config, pipeline_output_dirs, test_repository)
    assert not proc.stopped()
    proc.stop()
    assert proc.stopped()


def test_processor_stop_is_idempotent(
    pipeline_config, pipeline_output_dirs, test_repository
):
    """Calling stop() twice is safe."""
    proc = _make_proc(pipeline_config, pipeline_output_dirs, test_repository)
    proc.stop()
    proc.stop()
    assert proc.stopped()


def test_processor_get_results_returns_empty_dataframe(
    pipeline_config, pipeline_output_dirs, test_repository
):
    """get_results() returns an empty DataFrame — results live in the repository."""
    proc = _make_proc(pipeline_config, pipeline_output_dirs, test_repository)
    result = proc.get_results()
    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_processor_save_results_returns_none(
    pipeline_config, pipeline_output_dirs, test_repository
):
    """save_results() is a no-op; persistence is handled by RepositoryWriter."""
    proc = _make_proc(pipeline_config, pipeline_output_dirs, test_repository)
    result = proc.save_results()
    assert result is None


def test_processor_close_database_returns_none(
    pipeline_config, pipeline_output_dirs, test_repository
):
    """close_database() is a no-op; the repository owns its own lifecycle."""
    proc = _make_proc(pipeline_config, pipeline_output_dirs, test_repository)
    result = proc.close_database()
    assert result is None


def test_processor_requires_repository(pipeline_config, pipeline_output_dirs):
    """RadarProcessor raises ValueError when repository is None."""
    with pytest.raises(ValueError, match="DataRepository is required"):
        RadarProcessor(queue.Queue(), pipeline_config, pipeline_output_dirs,
                       repository=None)
