# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Headless radar command: target selection file in, published command file out.

Every decision is recorded in the ledger. Reads the store only through
adapt.api (via source.py); never talks to the radar.
"""

import logging
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from adapt.api import StoreClient, StoreError
from adapt.consumers.radar_command.command import CommandValidationError, to_json
from adapt.consumers.radar_command.config import RadarCommandConfig, config_hash
from adapt.consumers.radar_command.ledger import Event, LedgerRecord, append, log_event, summary
from adapt.consumers.radar_command.plan import build_plan
from adapt.consumers.radar_command.publish import OutputPaths, publish
from adapt.consumers.radar_command.selection import read_selection
from adapt.consumers.radar_command.source import TargetUnavailable, load_target

logger = logging.getLogger(__name__)

# Outcomes where a selection that names a cell got no command published.
FAILED_OUTCOMES = frozenset({Event.TARGET_UNAVAILABLE, Event.VALIDATION_FAILED, Event.OUT_OF_ORDER})


@dataclass(frozen=True)
class RadarCommandJob:
    """Which selection file to act on, and whether to keep watching it."""

    selection_path: Path
    follow: bool
    poll_seconds: float


def run(client: StoreClient, cfg: RadarCommandConfig, job: RadarCommandJob) -> LedgerRecord:
    """Process the selection file once and return the outcome as logged; with
    ``job.follow``, process it whenever it names a new scan.

    Following never returns: it polls every ``job.poll_seconds``, logs every
    outcome, and waits while the selection file does not exist yet. A
    TARGET_UNAVAILABLE selection is retried at every poll until it resolves or a
    new selection replaces it: data such as cell_volume_stats lands after the
    scan is complete.
    """
    if not job.follow:
        return process_selection(client, cfg, job.selection_path)
    done: tuple[str, str] | None = None
    while True:
        if job.selection_path.exists():
            ref = read_selection(job.selection_path)
            if (ref.run_id, ref.scan_id) != done:
                # Processing re-reads the file, which may have been replaced since:
                # what is done is the selection the outcome records.
                outcome = _process(client, cfg, job.selection_path)
                if outcome.event is not Event.TARGET_UNAVAILABLE:
                    done = (outcome.run_id, outcome.scan_id)
        time.sleep(job.poll_seconds)


def _process(client: StoreClient, cfg: RadarCommandConfig, selection_path: Path) -> LedgerRecord:
    outcome = process_selection(client, cfg, selection_path)
    level = logging.WARNING if outcome.event in FAILED_OUTCOMES else logging.INFO
    logger.log(level, summary(outcome))
    return outcome


def process_selection(
    client: StoreClient, cfg: RadarCommandConfig, selection_path: Path
) -> LedgerRecord:
    """Plan and publish a command for the selection at ``selection_path``; return the
    outcome as logged."""
    output = Path(cfg.output.directory)
    history = output / cfg.output.history_directory
    paths = OutputPaths(
        output / cfg.output.current_file,
        history,
        history / "activity.jsonl",
        history / "publish.lock",
    )
    history.mkdir(parents=True, exist_ok=True)
    ref = read_selection(selection_path)
    record = LedgerRecord(
        event=Event.SELECTION_RECEIVED,
        recorded_at=datetime.now(UTC),
        run_id=ref.run_id,
        scan_id=ref.scan_id,
        source_scan_time=ref.scan_time,
        cell_uid=ref.cell_uid,
        config_hash=config_hash(cfg),
    )
    append(paths.ledger, record)
    if ref.cell_uid is None:
        return log_event(paths.ledger, record, Event.NO_TARGET)
    try:
        plan = build_plan(load_target(client, ref, cfg.extent.vertical_band), ref, cfg)
    except (TargetUnavailable, StoreError) as exc:
        detail = f"{type(exc).__name__}: {exc}"
        return log_event(paths.ledger, record, Event.TARGET_UNAVAILABLE, detail)
    record = replace(
        record,
        plan_id=plan.plan_id,
        n_entries=len(plan.command.entries),
        truncated=plan.truncated,
        vertical_source=plan.vertical_source,
    )
    log_event(paths.ledger, record, Event.PLAN_GENERATED)
    try:
        command_json = to_json(plan.command)
    except CommandValidationError as exc:
        return log_event(paths.ledger, record, Event.VALIDATION_FAILED, str(exc))
    return publish(command_json, record, paths)
