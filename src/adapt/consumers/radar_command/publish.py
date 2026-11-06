# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Publish a radar command: history snapshot, then the atomic current file, then the ledger.

The history snapshot is written before the current file so every command the
radar could ever have read is archived. Directories are created by the runner.
Both files are world-readable (0644): a command carries no credential (the
transport injects the ApiToken when sending) and the radar-side reader may run
as another user.
"""

from dataclasses import dataclass
from pathlib import Path

from adapt.consumers.radar_command.ledger import Event, LedgerRecord, log_event, read_records
from adapt.consumers.radar_command.lock import exclusive_lock
from adapt.utils.files import atomic_write_text
from adapt.utils.time import to_scan_iso


@dataclass(frozen=True)
class OutputPaths:
    current: Path
    history_dir: Path
    ledger: Path
    lock: Path


def publish(command_json: str, record: LedgerRecord, paths: OutputPaths) -> LedgerRecord:
    """Publish ``command_json`` for the plan in ``record``; return the outcome as logged.

    Only PUBLISHED ledger records guard: a plan_id already published is a
    DUPLICATE; a scan no newer than the latest published one is OUT_OF_ORDER.
    A failed write is logged as PUBLISH_FAILED and re-raised, current untouched.
    The guard and the writes run under ``paths.lock``, so publishers sharing an
    output directory never interleave.
    """
    with exclusive_lock(paths.lock):
        return _publish(command_json, record, paths)


def _publish(command_json: str, record: LedgerRecord, paths: OutputPaths) -> LedgerRecord:
    published = [r for r in read_records(paths.ledger) if r.event is Event.PUBLISHED]
    if any(r.plan_id == record.plan_id for r in published):
        return log_event(paths.ledger, record, Event.DUPLICATE)
    newer = [r.source_scan_time for r in published if r.source_scan_time >= record.source_scan_time]
    if newer:
        detail = f"a plan for scan time {to_scan_iso(max(newer))} is already published"
        return log_event(paths.ledger, record, Event.OUT_OF_ORDER, detail)
    try:
        atomic_write_text(paths.history_dir / f"{record.plan_id}.json", command_json)
        atomic_write_text(paths.current, command_json)
    except Exception as exc:
        log_event(paths.ledger, record, Event.PUBLISH_FAILED, f"{type(exc).__name__}: {exc}")
        raise
    return log_event(paths.ledger, record, Event.PUBLISHED)
