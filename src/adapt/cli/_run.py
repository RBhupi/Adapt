# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt run NAME``: run the pipeline file ``./NAME.yaml``; the current folder is the store.

The file is named with or without its extension: ``adapt run nexrad.yaml`` is
``adapt run nexrad``.

The whole file is checked first — the run section, every listed module
(configured as it will run, with every input it reads accounted for), the
source, and the radar API settings in the environment (base URL, key, CA file)
when arm_radar_comm is to send — and nothing starts if any check fails.

The orchestrator then runs on its own thread until the source is exhausted
(historical), the time is up, or Ctrl-C / SIGTERM stops it.

``--run-id`` of a run that stopped resumes it from its last checkpoint: it goes
on with the scan after its last complete one, exactly as if it had not stopped.

``run.source: stored_run`` runs the listed modules over a run the store already
has (``--run-id`` names it): nothing is acquired, and what those modules read
from modules not listed is read back from the store.

``--set params.<module, source or section>.<option>=<value>`` sets an option for this
run, over what the file says; the run records it. A resumed run keeps the
options it recorded, so ``--set`` is refused there.
"""

import logging
import signal
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adapt.cli._pid import check_single_instance, remove_pid, write_pid
from adapt.configuration.pipeline_file import load_pipeline_file
from adapt.configuration.pipeline_run import run_config
from adapt.configuration.pipeline_template import pipeline_template_names
from adapt.configuration.schemas.errors import ConfigError
from adapt.persistence.store import Store
from adapt.runtime.orchestrator import PipelineOrchestrator
from adapt.runtime.sources import check_source

__all__ = ["RunFlags", "pipeline_path", "run"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Ending:
    """How a run ended: its status, how many scans failed, and whether a stop
    was requested (Ctrl-C or SIGTERM)."""

    status: str
    failed_scans: int
    interrupted: bool


@dataclass(frozen=True)
class RunFlags:
    """``verbose``: DEBUG logging. ``skip_failed``: resuming a run, leave the
    scans it failed before as they are instead of trying them again."""

    verbose: bool
    skip_failed: bool


def run(name: str, run_id: str | None, overrides: Sequence[str], flags: RunFlags) -> bool:
    """Run ``./NAME.yaml`` with the command line's ``overrides`` (``--set``
    settings) laid over it; return whether it succeeded (exit code 0).

    Raises
    ------
    StoreError
        The current folder is not a store.
    ConfigError
        The file is missing or invalid, or does not fit how it is run.
    """
    root = Store.open(Path.cwd()).root
    path = pipeline_path(root, name, "run")
    pipeline = load_pipeline_file(path, overrides)
    config = run_config(pipeline, path, run_id)  # configures every module
    check_source(config)  # e.g. the folder a local run reads
    if flags.verbose:
        logging_ = config.logging.model_copy(update={"level": "DEBUG", "console_level": "DEBUG"})
        config = config.model_copy(update={"logging": logging_})
    check_single_instance()
    write_pid()
    try:
        orchestrator = PipelineOrchestrator(config, skip_failed=flags.skip_failed)
        ending = _supervise(orchestrator, pipeline.run.max_runtime_minutes)
    finally:
        remove_pid()
    if ending.failed_scans:
        print(f"Scans that failed: {ending.failed_scans} (see the run's log and run_events)")
    return ending.status == "completed" and not ending.failed_scans and not ending.interrupted


def pipeline_path(root: Path, name: str, command: str) -> Path:
    """``root/NAME.yaml`` for ``adapt <command> NAME`` or ``adapt <command> NAME.yaml``;
    it must exist, and the error says how to make it."""
    if Path(name).name != name:
        raise ConfigError(
            f"adapt {command} takes the name of a pipeline file in the current folder, NAME "
            f"or NAME.yaml, not a path (e.g. 'adapt {command} nexrad.yaml'); got '{name}'"
        )
    name = name.removesuffix(".yaml")
    path = root / f"{name}.yaml"
    if path.is_file():
        return path
    templates = pipeline_template_names()
    if name in templates:
        hint = f"Write it from its template with:  adapt config --{name}"
    else:
        template = templates[0]
        hint = (
            f"Start it from a template's file, e.g.:  cp {template}.yaml {name}.yaml  "
            f"(adapt config --{template} writes {template}.yaml; templates: "
            f"{', '.join(templates)})"
        )
    raise ConfigError(f"No pipeline file {path}. {hint}")


def _supervise(orchestrator: PipelineOrchestrator, max_runtime_minutes: int | None) -> _Ending:
    """Run the pipeline to the end.

    The status is the run's terminal one, or ``unfinished`` when a second Ctrl-C
    abandoned the shutdown. A pipeline that raised re-raises here.
    """
    outcome: dict[str, Any] = {}
    interrupted = threading.Event()
    # Set by the pipeline thread itself. Thread.is_alive() is not trusted: on
    # Python 3.12 a Ctrl-C that interrupts join() marks a live thread as stopped.
    finished = threading.Event()

    def pipeline() -> None:
        try:
            outcome["status"] = orchestrator.start(max_runtime_minutes)
        except Exception as exc:  # re-raised on the main thread below
            outcome["error"] = exc
        finally:
            finished.set()

    def stop(*_: object) -> None:
        interrupted.set()
        orchestrator.request_stop()  # finalizes the run as 'cancelled'

    thread = threading.Thread(target=pipeline, name="Pipeline", daemon=False)
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        thread.start()
        try:
            _wait(finished)
        except KeyboardInterrupt:
            print("\nStopping the pipeline...")
            stop()
            try:
                _wait(finished)  # every step of the shutdown is bounded
            except KeyboardInterrupt:
                print("[adapt] Forcing shutdown...")
    finally:
        signal.signal(signal.SIGTERM, previous)
    if "error" in outcome:
        raise outcome["error"]
    status = outcome["status"] if finished.is_set() else "unfinished"
    summary = orchestrator.summary  # None only for a run abandoned before it was finalized
    failed_scans = summary.failures if summary is not None else 0
    return _Ending(status, failed_scans, interrupted.is_set())


def _wait(finished: threading.Event) -> None:
    """Wait for ``finished`` in short steps, so Ctrl-C is seen at once."""
    while not finished.wait(0.5):
        pass
