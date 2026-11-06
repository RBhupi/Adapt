# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Cross-process exclusive lock guarding publication into one output directory."""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

if sys.platform == "win32":
    import msvcrt

    def _lock(fh: BinaryIO) -> None:
        # Mandatory byte-range lock on byte 0; LK_LOCK retries for ~10 s, then raises.
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock(fh: BinaryIO) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fh: BinaryIO) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def _unlock(fh: BinaryIO) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_lock(path: str | Path) -> Iterator[None]:
    """Hold an exclusive lock on ``path`` (created if absent) for the block.

    Waits while another process holds it. The OS drops the lock when its holder
    exits, so a crash never leaves a stale lock behind.
    """
    with open(path, "a+b") as fh:
        _lock(fh)
        try:
            yield
        finally:
            _unlock(fh)
