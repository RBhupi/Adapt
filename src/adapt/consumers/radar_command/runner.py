# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Headless radar command: target selection file in, published command file out.

Every decision is recorded in the ledger. Reads the store only through
adapt.api (via source.py). With ``api`` configured, each newly published
command is also POSTed to the radar API; without it, nothing leaves the files.
"""

import logging
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr

from adapt.api import StoreClient, StoreError
from adapt.consumers.radar_command.command import CommandValidationError, to_json
from adapt.consumers.radar_command.config import RadarCommandConfig, config_hash
from adapt.consumers.radar_command.ledger import Event, LedgerRecord, append, log_event, summary
from adapt.consumers.radar_command.plan import build_plan
from adapt.consumers.radar_command.publish import OutputPaths, publish
from adapt.consumers.radar_command.selection import read_selection
from adapt.consumers.radar_command.source import TargetUnavailable, load_target
from adapt.consumers.radar_command.transport import SendError, api_key, send

logger = logging.getLogger(__name__)

# Outcomes where a selection that names a cell got no command published.
FAILED_OUTCOMES = frozenset(
    {Event.TARGET_UNAVAILABLE, Event.VALIDATION_FAILED, Event.OUT_OF_ORDER, Event.SEND_FAILED}
)


@dataclass(frozen=True)
class RadarCommandJob:
    """Which selection file to follow, where to write, and when to stop."""

    selection_path: Path
    output_dir: Path
    poll_seconds: float
    stop: threading.Event


def run(client: StoreClient, cfg: RadarCommandConfig, job: RadarCommandJob) -> None:
    """Follow the selection file, processing it whenever it names a new scan,
    until ``job.stop`` is set.

    It polls every ``job.poll_seconds``, logs every outcome, and waits while the
    selection file does not exist yet. A TARGET_UNAVAILABLE selection is retried
    at every poll until it resolves or a new selection replaces it: data such as
    cell_volume_stats lands after the scan is complete. Once stopped, one last
    poll still processes the final selection. With ``cfg.api``, its key is read
    once, first: a missing key raises ValueError before any selection is processed.
    """
    key = None if cfg.api is None else api_key(cfg.api)
    done: tuple[str, str] | None = None
    while True:
        # Read before the poll, so a selection written just before the stop is
        # still processed by it.
        last = job.stop.is_set()
        if job.selection_path.exists():
            ref = read_selection(job.selection_path)
            if (ref.run_id, ref.scan_id) != done:
                # Processing re-reads the file, which may have been replaced since:
                # what is done is the selection the outcome records.
                outcome = _process(client, cfg, job, key)
                if outcome.event is not Event.TARGET_UNAVAILABLE:
                    done = (outcome.run_id, outcome.scan_id)
        if last:
            return
        job.stop.wait(job.poll_seconds)


def _process(
    client: StoreClient, cfg: RadarCommandConfig, job: RadarCommandJob, key: SecretStr | None
) -> LedgerRecord:
    outcome = process_selection(client, cfg, job.selection_path, job.output_dir, key)
    level = logging.WARNING if outcome.event in FAILED_OUTCOMES else logging.INFO
    logger.log(level, summary(outcome))
    return outcome


def process_selection(
    client: StoreClient,
    cfg: RadarCommandConfig,
    selection_path: Path,
    output_dir: Path,
    key: SecretStr | None = None,
) -> LedgerRecord:
    """Plan and publish a command for the selection at ``selection_path``, and send
    it when ``cfg.api`` is set (``key`` is then required); return the outcome as logged.

    Writes ``output_dir/current_targets.json`` and, in ``output_dir/history/``,
    every published plan, the ``activity.jsonl`` ledger and the publish lock.
    """
    history = output_dir / "history"
    paths = OutputPaths(
        output_dir / "current_targets.json",
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
        selection_config_hash=ref.config_hash,
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
    published = publish(command_json, record, paths)
    if cfg.api is None or published.event is not Event.PUBLISHED:
        return published
    if key is None:
        raise ValueError("radar_command.api is configured but no API key was given")
    try:
        response = send(command_json, cfg.api, key)
    except SendError as exc:
        return log_event(paths.ledger, published, Event.SEND_FAILED, str(exc))
    return log_event(paths.ledger, published, Event.SENT, response)
