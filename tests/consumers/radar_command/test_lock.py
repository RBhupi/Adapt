# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Cross-process publication lock."""

import threading

import pytest

from adapt.consumers.radar_command.lock import exclusive_lock

pytestmark = pytest.mark.unit


def test_exclusive_lock_makes_a_second_holder_wait_for_the_first(tmp_path):
    lock = tmp_path / "publish.lock"
    acquired = threading.Event()

    def second_holder():
        with exclusive_lock(lock):
            acquired.set()

    waiter = threading.Thread(target=second_holder)
    with exclusive_lock(lock):
        waiter.start()
        assert not acquired.wait(0.3)  # blocked while the first holds it
    assert acquired.wait(5.0)  # proceeds once the first lets go
    waiter.join()
