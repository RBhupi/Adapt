# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The stored-run source: the scans a run of the store already has, in time order."""

import queue
from datetime import UTC, datetime

import pytest

from adapt.sources.stored_run.source import StoredRunSource

pytestmark = pytest.mark.unit


def test_queues_the_runs_stored_scans_in_time_order_and_reports_complete(
    tmp_path, store_env, internal_config
):
    later = datetime(2024, 5, 18, 12, 5, tzinfo=UTC)
    earlier = datetime(2024, 5, 18, 12, 0, tzinfo=UTC)
    scans = (("KLOT20240518_120500_V06", later), ("KLOT20240518_120000_V06", earlier))
    for name, scan_time in scans:
        path = tmp_path / name
        path.write_bytes(name.encode())
        store_env.acquirer.acquire_file(path, source_uri=str(path), scan_time=scan_time)
    q: queue.Queue = queue.Queue()

    source = StoredRunSource(config=internal_config, result_queue=q, acquire=store_env.acquirer)
    source.run()

    assert [q.get_nowait()["scan_time"] for _ in range(q.qsize())] == [earlier, later]
    assert source.is_historical_complete() is True
    assert source.get_historical_progress() == (2, 2)
