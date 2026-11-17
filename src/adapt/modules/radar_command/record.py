# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""What radar_command did at one scan, as the row the store keeps.

One row per scan and config in ``radar_commands``: the selected cell, the
outcome, and — when a command was planned — its id and its JSON as sent on the
wire. ``detail`` says how the send went (the acknowledgment, or the status, the
start of the answer and the address) or why there is no command.
"""

from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

from adapt.utils.frames import REAL, TEXT, typed_frame

__all__ = ["CommandRecord", "Outcome", "command_frame", "summary"]


class Outcome(StrEnum):
    NO_TARGET = "NO_TARGET"  # target selection chose no cell
    TARGET_UNAVAILABLE = "TARGET_UNAVAILABLE"  # the cell has no projected footprint or extent
    VALIDATION_FAILED = "VALIDATION_FAILED"  # the planned command is not a valid one
    DUPLICATE = "DUPLICATE"  # this plan was already published by this run
    PUBLISHED = "PUBLISHED"  # the command is the scan's command (recorded, not sent)
    STALE = "STALE"  # ... and was held back: the scan was older than the limit for sending
    SENT = "SENT"  # ... and the radar API acknowledged it as queued (not as run by the radar)
    NOT_ACKNOWLEDGED = (
        "NOT_ACKNOWLEDGED"  # ... it went to the radar API, which did not acknowledge it
    )
    SEND_FAILED = "SEND_FAILED"  # ... but it did not go out to the radar API


FAILED = frozenset(
    {
        Outcome.TARGET_UNAVAILABLE,
        Outcome.VALIDATION_FAILED,
        Outcome.NOT_ACKNOWLEDGED,
        Outcome.SEND_FAILED,
    }
)


@dataclass(frozen=True)
class CommandRecord:
    """One scan's outcome. ``config_hash`` identifies the radar command config;
    ``selection_config_hash`` the target selection rules that chose the cell."""

    outcome: Outcome
    config_hash: str
    selection_config_hash: str
    cell_uid: str | None = None
    detail: str | None = None
    plan_id: str | None = None
    n_entries: int | None = None
    truncated: bool | None = None
    vertical_source: str | None = None
    command_json: str | None = None


_COLUMNS = (
    ("config_hash", TEXT),
    ("selection_config_hash", TEXT),
    ("cell_uid", TEXT),
    ("outcome", TEXT),
    ("detail", TEXT),
    ("plan_id", TEXT),
    ("n_entries", REAL),
    ("truncated", REAL),
    ("vertical_source", TEXT),
    ("command_json", TEXT),
)


def command_frame(record: CommandRecord) -> pd.DataFrame:
    """The one-row ``radar_commands`` frame of ``record``."""
    row = {name: getattr(record, name) for name, _ in _COLUMNS}
    row["outcome"] = record.outcome.value
    row["truncated"] = None if record.truncated is None else float(record.truncated)
    return typed_frame([row], _COLUMNS)


_CONSOLE_CHARS = 90  # how much of a reason the console line shows; the table holds it all
# A send at a glance, as two marks: did the command go out to the radar API, and
# did the API acknowledge it as queued (no second mark when nothing went out).
_MARKS = {Outcome.SENT: "✓✓ ", Outcome.NOT_ACKNOWLEDGED: "✓✗ ", Outcome.SEND_FAILED: "✗– "}
_FATES = {
    Outcome.PUBLISHED: "written",
    Outcome.STALE: "held back",
    Outcome.SENT: "sent",
    Outcome.NOT_ACKNOWLEDGED: "not acknowledged",
    Outcome.SEND_FAILED: "not sent",
}


def summary(record: CommandRecord) -> str:
    """One short console line: what happened to which plan and cell, and why."""
    plan = f" {record.plan_id}" if record.plan_id is not None else ""
    if record.outcome in _FATES:
        what = f"command{plan} {_FATES[record.outcome]}"
    else:
        what = record.outcome.value.lower().replace("_", " ") + plan
    cell = f" for cell {record.cell_uid}" if record.cell_uid is not None else ""
    return f"{_MARKS.get(record.outcome, '')}radar_command: {what}{cell}{_note(record)}"


def _note(record: CommandRecord) -> str:
    if record.outcome is Outcome.PUBLISHED:
        return " (dry run, nothing sent)"
    if record.outcome is Outcome.SENT:
        return " (queued by the radar API)"
    if record.detail is None:
        return ""
    cut = len(record.detail) > _CONSOLE_CHARS
    return f": {record.detail[:_CONSOLE_CHARS]}{'…' if cut else ''}"
