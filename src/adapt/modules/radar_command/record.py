# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""What radar_command did at one scan, as the row the store keeps.

One row per scan and config in ``radar_commands``: the selected cell, the
outcome, and — when a command was planned — its id and its JSON as sent on the
wire. ``detail`` carries what the radar API answered, or why there is no command.
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
    SENT = "SENT"  # ... and the radar API accepted it
    SEND_FAILED = "SEND_FAILED"  # ... but the radar API did not


FAILED = frozenset({Outcome.TARGET_UNAVAILABLE, Outcome.VALIDATION_FAILED, Outcome.SEND_FAILED})


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


_COMMAND_FATES = {Outcome.PUBLISHED: "written", Outcome.SENT: "sent"}


def summary(record: CommandRecord, run_id: str, scan_id: str) -> str:
    """One human-readable line: what happened, to which plan, cell and scan, and any detail."""
    plan = f" {record.plan_id}" if record.plan_id is not None else ""
    if record.outcome in _COMMAND_FATES:
        what = f"command{plan} {_COMMAND_FATES[record.outcome]}"
    else:
        what = record.outcome.value.lower().replace("_", " ") + plan
    cell = f" for cell {record.cell_uid}" if record.cell_uid is not None else ""
    detail = f": {record.detail}" if record.detail is not None else ""
    return f"radar_command: {what}{cell} at scan {scan_id} of run {run_id}{detail}"
