# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""One live pipeline per user: the PID file ``~/.adapt/pipeline.pid``.

The dashboard reads the same file to tell whether a pipeline is running.
"""

import logging
import os
import sys
from pathlib import Path

from adapt.utils.process import process_alive

logger = logging.getLogger(__name__)


def pid_file() -> Path:
    return Path.home() / ".adapt" / "pipeline.pid"


def check_single_instance() -> None:
    """Exit with an error if another pipeline is already running."""
    path = pid_file()
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8").strip()
    if not text.isdigit():
        print(f"[adapt] Error: The PID file {path} does not hold a process id: {text!r}.")
        print(f"[adapt] Delete {path} if no pipeline is running.")
        sys.exit(1)
    pid = int(text)
    if process_alive(pid):
        print(f"[adapt] Error: A pipeline is already running (PID {pid}).")
        print(f"[adapt] Stop it first, or delete {path} if it is stale.")
        sys.exit(1)


def write_pid() -> None:
    path = pid_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="utf-8")


def remove_pid() -> None:
    path = pid_file()
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove PID file %s: %s", path, exc)
