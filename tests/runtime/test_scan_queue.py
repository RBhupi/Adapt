# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The scan queue holds a source at its high level until it drains to its low one.

Only the source waits; the processor keeps consuming. The two levels are apart
so a full queue does not turn into one fetch per consumed scan.
"""

import time

import pytest

from adapt.runtime.scan_queue import BackpressureQueue

pytestmark = pytest.mark.unit


def _never_stopped() -> bool:
    return False


def _draining(high: int, frac: float, n: int, seen: list[int], pause: float = 0.0):
    """A queue holding ``n`` scans whose every poll lets a processor take one.

    ``seen`` records the depth at each poll.
    """

    def consume_one(_seconds: float) -> None:
        time.sleep(pause)
        seen.append(q.qsize())
        q.get_nowait()

    q = BackpressureQueue(high, frac, sleep=consume_one)
    for i in range(n):
        q.put(f"scan-{i}")
    return q


def test_a_source_is_not_held_below_the_high_level():
    seen: list[int] = []
    q = _draining(4, 0.5, 3, seen)

    q.wait_for_room(_never_stopped)

    assert seen == []
    assert q.qsize() == 3
    assert q.backpressure_stats()["pauses"] == 0


def test_a_source_is_held_at_the_high_level_until_the_queue_drains_to_the_low_level():
    seen: list[int] = []
    q = _draining(4, 0.5, 4, seen)

    q.wait_for_room(_never_stopped)

    assert seen == [4, 3]  # released once the depth is 2 = 4 * 0.5
    assert q.qsize() == 2


def test_the_low_level_is_at_least_one_scan():
    seen: list[int] = []
    q = _draining(3, 0.1, 3, seen)

    q.wait_for_room(_never_stopped)

    assert seen == [3, 2]  # int(3 * 0.1) is 0; the source waits for 1, not 0
    assert q.qsize() == 1


def test_a_stop_request_releases_a_held_source():
    polls: list[float] = []
    q = BackpressureQueue(2, 0.5, sleep=polls.append)
    q.put("scan-0")
    q.put("scan-1")

    q.wait_for_room(lambda: bool(polls))

    assert len(polls) == 1  # one poll, then the stop released it
    assert q.qsize() == 2  # nothing consumed


def test_pauses_and_paused_seconds_are_counted():
    seen: list[int] = []
    q = _draining(2, 0.5, 2, seen, pause=0.06)

    q.wait_for_room(_never_stopped)
    q.put("scan-2")
    q.wait_for_room(_never_stopped)

    stats = q.backpressure_stats()
    assert stats["pauses"] == 2
    assert stats["paused_seconds"] >= 0.1  # two polls of at least 0.06 s each
    assert (stats["queue_high"], stats["queue_low"]) == (2, 1)
