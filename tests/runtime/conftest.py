import queue
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from adapt.configuration.schemas.internal import InternalConfig
from adapt.runtime.processor import RadarProcessor
from tests.helpers.run_configs import new_run_config


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d)


@pytest.fixture
def make_processor(store_env, pipeline_config):
    """Factory for a store-wired RadarProcessor."""

    def _make(input_queue=None, config=None, **kw):
        return RadarProcessor(
            input_queue if input_queue is not None else queue.Queue(),
            config or pipeline_config,
            collection=store_env.collection,
            registry=store_env.registry,
            run_id=store_env.run_id,
            history=store_env.history,
            **kw,
        )

    return _make


@pytest.fixture
def make_scan_message(store_env, temp_dir):
    """Acquire a synthetic raw file into the store; returns the queue message."""

    def _make(name="TEST_20240518_120000", scan_time=None, payload=None):
        path = temp_dir / name
        path.write_bytes(payload if payload is not None else name.encode())
        return store_env.acquirer.acquire_file(
            path,
            source_uri=str(path),
            scan_time=scan_time or datetime(2024, 5, 18, 12, 0, 0, tzinfo=UTC),
        )

    return _make


@pytest.fixture
def pipeline_config(store_root) -> InternalConfig:
    """The config of a new realtime run of radar TEST_RADAR in the store at
    ``store_root``: the modules of a plain NEXRAD run (a test of another set
    says so), every setting and option at its default."""
    return new_run_config(store_root)


# made for processor tests
@pytest.fixture
def processor_queues():
    return queue.Queue(), queue.Queue()
