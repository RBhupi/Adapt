# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Append-only JSONL audit ledger of every radar command decision.

JSONL, not sqlite: consumers never open databases. ``recorded_at`` is the
wall-clock time of the decision and the basis of plan age for a future
stale-command policy; ``source_scan_time`` is the scan the decision came from.
"""

import json
import os
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from adapt.utils.time import from_scan_iso, to_scan_iso


class Event(StrEnum):
    SELECTION_RECEIVED = "SELECTION_RECEIVED"
    NO_TARGET = "NO_TARGET"
    TARGET_UNAVAILABLE = "TARGET_UNAVAILABLE"
    PLAN_GENERATED = "PLAN_GENERATED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    PUBLISHED = "PUBLISHED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    SENT = "SENT"
    SEND_FAILED = "SEND_FAILED"


@dataclass(frozen=True)
class LedgerRecord:
    """One ledger line: what happened, when, and for which scan, cell and config.

    ``config_hash`` identifies the radar command config; ``selection_config_hash``
    the target selection rules that chose the cell.
    """

    event: Event
    recorded_at: datetime
    run_id: str
    scan_id: str
    source_scan_time: datetime
    cell_uid: str | None
    config_hash: str
    selection_config_hash: str
    plan_id: str | None = None
    n_entries: int | None = None
    truncated: bool | None = None
    vertical_source: str | None = None
    detail: str | None = None


def append(path: Path, record: LedgerRecord) -> None:
    """Append ``record`` as one JSON line and fsync. The parent directory must exist."""
    obj = asdict(record) | {
        "event": record.event.value,
        "recorded_at": record.recorded_at.isoformat(),
        "source_scan_time": to_scan_iso(record.source_scan_time),
    }
    # newline="\n": the ledger is a data product; its bytes must not vary by platform.
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def log_event(
    path: Path, record: LedgerRecord, event: Event, detail: str | None = None
) -> LedgerRecord:
    """Append ``record`` as ``event`` stamped now; return the line as written."""
    logged = replace(record, event=event, recorded_at=datetime.now(UTC), detail=detail)
    append(path, logged)
    return logged


_COMMAND_FATES = {Event.PUBLISHED: "written", Event.SENT: "sent"}


def summary(record: LedgerRecord) -> str:
    """One human-readable line: what happened, to which plan, cell and scan, and any detail."""
    plan = f" {record.plan_id}" if record.plan_id is not None else ""
    if record.event in _COMMAND_FATES:
        what = f"command{plan} {_COMMAND_FATES[record.event]}"
    else:
        what = record.event.value.lower().replace("_", " ") + plan
    cell = f" for cell {record.cell_uid}" if record.cell_uid is not None else ""
    detail = f": {record.detail}" if record.detail is not None else ""
    return f"radar_command: {what}{cell} at scan {record.scan_id} of run {record.run_id}{detail}"


def read_records(path: Path) -> list[LedgerRecord]:
    """Return every ledger record in file order; a missing file means no history yet."""
    if not path.exists():
        return []
    records = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            records.append(_decode(line))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{path}: malformed ledger record on line {lineno}: {exc}") from exc
    return records


_KEYS = frozenset(f.name for f in fields(LedgerRecord))


def _decode(line: str) -> LedgerRecord:
    obj = json.loads(line)
    if not isinstance(obj, dict) or obj.keys() != _KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_KEYS)}")
    return LedgerRecord(
        **obj
        | {
            "event": Event(obj["event"]),
            "recorded_at": datetime.fromisoformat(obj["recorded_at"]),
            "source_scan_time": from_scan_iso(obj["source_scan_time"]),
        }
    )
