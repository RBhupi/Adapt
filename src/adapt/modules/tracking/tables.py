# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""One scan's tracking tables as rows: cell identity, lineage events, track lifecycle.

``scan_tables`` turns the tracker's scan-local output into the rows the store
keeps, with nothing left for the store to work out:

- ``cell_identity`` — one row per cell of the scan: its label, its uid, and
  what is known about it at this scan (age, neighbours, how it began).
- ``cell_events`` — the scan's lineage events, each naming the scan its source
  cell was last observed in.
- ``cell_tracks`` — the lifecycle row of every track this scan touched.

What a later scan needs from an earlier one travels in ``Lifecycle``; the
function itself keeps nothing.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from adapt.utils.frames import INT as _INT
from adapt.utils.frames import REAL as _REAL
from adapt.utils.frames import TEXT as _TEXT
from adapt.utils.frames import integer_columns, typed_frame
from adapt.utils.time import from_scan_iso, to_scan_iso

__all__ = [
    "EVENT_INTEGER_COLUMNS",
    "IDENTITY_INTEGER_COLUMNS",
    "TRACK_INTEGER_COLUMNS",
    "Lifecycle",
    "ScanTables",
    "scan_tables",
]

_IDENTITY_COLUMNS = (
    ("cell_label", _INT),
    ("cell_uid", _TEXT),
    ("age_seconds", _REAL),
    ("n_adjacent_cells", _INT),
    ("adjacent_cell_uids_json", _TEXT),
    ("is_initiated_here", _INT),
    ("is_split_target_here", _INT),
    ("is_merge_target_here", _INT),
    # A merge ends the absorbed cell's track and the survivor keeps its own uid
    # and age. These name the absorbed cell and the lifetime it had reached at
    # this scan (the oldest one, when several merge into the same target), so
    # the longer history stays recoverable. Identity is not transferred.
    ("merged_from_cell_uid", _TEXT),
    ("merged_from_age_seconds", _REAL),
)

_EVENT_COLUMNS = (
    ("event_seq", _INT),
    ("source_scan_id", _TEXT),
    ("target_scan_id", _TEXT),
    ("source_scan_time", _TEXT),
    ("target_scan_time", _TEXT),
    ("event_type", _TEXT),
    ("source_cell_uid", _TEXT),
    ("target_cell_uid", _TEXT),
    ("source_cell_label", _REAL),  # absent for some events, so NaN-able
    ("target_cell_label", _REAL),
    ("cost", _REAL),
    ("is_dominant", _INT),
    ("event_group_id", _TEXT),
    ("candidate_opc", _REAL),
    ("candidate_ocp", _REAL),
    ("candidate_centroid_distance_m", _REAL),
    ("candidate_speed_ms", _REAL),
    ("candidate_heading_change_deg", _REAL),
    ("candidate_area_ratio", _REAL),
    ("candidate_final_cost", _REAL),
    ("match_method", _TEXT),
)

_TRACK_COLUMNS = (
    ("cell_uid", _TEXT),
    ("first_seen_time", _TEXT),
    ("last_seen_time", _TEXT),
    ("n_scans", _INT),
    ("origin_type", _TEXT),
    ("origin_event_group_id", _TEXT),
    ("origin_n_parents", _INT),
    ("origin_primary_parent_cell_uid", _TEXT),
    ("termination_type", _TEXT),
    ("termination_event_group_id", _TEXT),
    ("terminated_into_cell_uid", _TEXT),
    ("duration_seconds", _REAL),
    ("max_area_sqkm", _REAL),
    ("max_reflectivity", _REAL),
)

IDENTITY_INTEGER_COLUMNS = integer_columns(_IDENTITY_COLUMNS)
EVENT_INTEGER_COLUMNS = (
    *integer_columns(_EVENT_COLUMNS),
    "source_cell_label",
    "target_cell_label",
)
TRACK_INTEGER_COLUMNS = integer_columns(_TRACK_COLUMNS)

# No target scan for an event that ends or suspends a track.
_UNTARGETED = frozenset({"TERMINATION", "LATENT"})


@dataclass(frozen=True)
class Lifecycle:
    """What the tracking tables of one scan need from the scans before it.

    ``tracks`` holds the lifecycle row of every track that may still change;
    ``last_scan`` is the (scan_id, scan time) of the latest scan that had cells.
    """

    tracks: dict[str, dict] = field(default_factory=dict)
    last_scan: tuple[str, str] | None = None


@dataclass(frozen=True)
class ScanTables:
    """The rows one scan adds, and the lifecycle the next scan starts from."""

    identity: pd.DataFrame
    events: pd.DataFrame
    tracks: pd.DataFrame
    lifecycle: Lifecycle


def scan_tables(
    tracked_cells: pd.DataFrame,
    cell_events: pd.DataFrame,
    cell_adjacency: pd.DataFrame,
    *,
    scan_id: str,
    scan_time: datetime,
    lifecycle: Lifecycle,
) -> ScanTables:
    """The tracking rows of scan ``scan_id``; a scan without cells adds none.

    Raises
    ------
    ValueError
        Two cells of the scan share a uid or a label, or the adjacency names a
        label that is not a cell of the scan.
    """
    if tracked_cells.empty:
        return ScanTables(
            typed_frame([], _IDENTITY_COLUMNS),
            typed_frame([], _EVENT_COLUMNS),
            typed_frame([], _TRACK_COLUMNS),
            lifecycle,
        )
    _reject_duplicate_identities(scan_id, tracked_cells)
    scan_iso = to_scan_iso(scan_time)
    events = [] if cell_events.empty else cell_events.to_dict("records")

    identity = _identity_rows(
        tracked_cells,
        events,
        _adjacent_uids(tracked_cells, cell_adjacency),
        scan_iso,
        {uid: row["first_seen_time"] for uid, row in lifecycle.tracks.items()},
    )
    event_rows = _event_rows(events, scan_id, scan_iso, lifecycle.last_scan)
    touched, tracks = _track_rows(tracked_cells, events, scan_iso, lifecycle.tracks)
    return ScanTables(
        typed_frame(identity, _IDENTITY_COLUMNS),
        typed_frame(event_rows, _EVENT_COLUMNS),
        typed_frame(touched, _TRACK_COLUMNS),
        Lifecycle(tracks=tracks, last_scan=(scan_id, scan_iso)),
    )


def _present(value) -> bool:
    return value is not None and not pd.isna(value)


# ── identity ────────────────────────────────────────────────────────────────


def _reject_duplicate_identities(scan_id: str, tracked_cells: pd.DataFrame) -> None:
    """A repeated uid or label within one scan would overwrite an observation."""
    uids = tracked_cells["cell_uid"].astype(str)
    labels = tracked_cells["cell_label"].astype(int)
    dup_uids = sorted(uids[uids.duplicated()].unique())
    dup_labels = sorted(int(v) for v in labels[labels.duplicated()].unique())
    if dup_uids or dup_labels:
        raise ValueError(
            f"duplicate cell identity within scan {scan_id}: "
            f"cell_uid={dup_uids}, cell_label={dup_labels}"
        )


def _adjacent_uids(
    tracked_cells: pd.DataFrame, cell_adjacency: pd.DataFrame
) -> dict[str, tuple[int, str]]:
    """Per cell uid: how many cells touch it, and their uids as a JSON list."""
    required = {"cell_label_a", "cell_label_b", "touching_boundary_pixels"}
    missing = sorted(required - set(cell_adjacency.columns))
    if missing:
        raise ValueError(f"cell_adjacency is missing required columns: {missing}")
    label_to_uid = {
        int(label): str(uid)
        for label, uid in zip(tracked_cells["cell_label"], tracked_cells["cell_uid"], strict=True)
    }
    neighbours: dict[str, set[str]] = {uid: set() for uid in label_to_uid.values()}
    for a, b in zip(cell_adjacency["cell_label_a"], cell_adjacency["cell_label_b"], strict=True):
        a, b = int(a), int(b)
        if a == b:
            raise ValueError("cell_adjacency contains a self-pair")
        if a not in label_to_uid or b not in label_to_uid:
            raise ValueError(f"cell_adjacency references unknown cell labels: {a}, {b}")
        neighbours[label_to_uid[a]].add(label_to_uid[b])
        neighbours[label_to_uid[b]].add(label_to_uid[a])
    return {uid: (len(adj), json.dumps(sorted(adj))) for uid, adj in neighbours.items()}


def _age_seconds(scan_iso: str, first_seen_iso: str) -> float:
    return max(0.0, (from_scan_iso(scan_iso) - from_scan_iso(first_seen_iso)).total_seconds())


def _identity_rows(
    tracked_cells: pd.DataFrame,
    events: list[dict],
    adjacency: dict[str, tuple[int, str]],
    scan_iso: str,
    first_seen: dict[str, str],
) -> list[dict]:
    begun: dict[str, set[int]] = {"INITIATION": set(), "SPLIT": set(), "MERGE": set()}
    # Absorbed-cell provenance per merge target: the oldest source wins, since
    # that is the history a reader would otherwise lose.
    merged_from: dict[int, tuple[str, float | None]] = {}
    for ev in events:
        etype, target_label = ev["event_type"], ev.get("target_cell_label")
        if etype not in begun or target_label is None or pd.isna(target_label):
            continue
        target = int(target_label)
        begun[etype].add(target)
        source_uid = ev.get("source_cell_uid")
        if etype != "MERGE" or not _present(source_uid) or not source_uid:
            continue
        uid = str(source_uid)
        age = _age_seconds(scan_iso, first_seen[uid]) if uid in first_seen else None
        kept = merged_from.get(target)
        if kept is None or (age or 0.0) > (kept[1] or 0.0):
            merged_from[target] = (uid, age)

    rows = []
    for label, uid in zip(tracked_cells["cell_label"], tracked_cells["cell_uid"], strict=True):
        label, uid = int(label), str(uid)
        known = label not in begun["INITIATION"] and uid in first_seen
        n_adjacent, adjacent_json = adjacency.get(uid, (0, "[]"))
        absorbed_uid, absorbed_age = merged_from.get(label, (None, None))
        rows.append(
            {
                "cell_label": label,
                "cell_uid": uid,
                "age_seconds": _age_seconds(scan_iso, first_seen[uid]) if known else 0.0,
                "n_adjacent_cells": n_adjacent,
                "adjacent_cell_uids_json": adjacent_json,
                "is_initiated_here": int(label in begun["INITIATION"]),
                "is_split_target_here": int(label in begun["SPLIT"]),
                "is_merge_target_here": int(label in begun["MERGE"]),
                "merged_from_cell_uid": absorbed_uid,
                "merged_from_age_seconds": absorbed_age,
            }
        )
    return rows


# ── events ──────────────────────────────────────────────────────────────────


def _source_scan(ev: dict, last_scan: tuple[str, str] | None) -> tuple[str, str] | None:
    """(scan_id, scan time) the source cell was last observed in.

    The latest scan with cells, unless the event names its own — a resumed or
    an expiring latent track refers back to the scan it vanished after.
    """
    scan_id = ev.get("source_scan_id")
    if not _present(scan_id) or scan_id == (last_scan[0] if last_scan else None):
        return last_scan
    return str(scan_id), to_scan_iso(pd.Timestamp(ev["source_scan_time"]).to_pydatetime())


def _event_rows(
    events: list[dict], scan_id: str, scan_iso: str, last_scan: tuple[str, str] | None
) -> list[dict]:
    def text(value):
        return str(value) if _present(value) else None

    def number(value):
        return float(value) if _present(value) else None

    rows = []
    for seq, ev in enumerate(events):
        etype = str(ev["event_type"])
        source = None if etype == "INITIATION" else _source_scan(ev, last_scan)
        targeted = etype not in _UNTARGETED
        rows.append(
            {
                "event_seq": seq,
                "source_scan_id": source[0] if source else None,
                "target_scan_id": scan_id if targeted else None,
                "source_scan_time": source[1] if source else None,
                "target_scan_time": scan_iso if targeted else None,
                "event_type": etype,
                "source_cell_uid": text(ev.get("source_cell_uid")),
                "target_cell_uid": text(ev.get("target_cell_uid")),
                "source_cell_label": number(ev.get("source_cell_label")),
                "target_cell_label": number(ev.get("target_cell_label")),
                "cost": number(ev.get("cost")),
                "is_dominant": int(bool(ev.get("is_dominant", False))),
                "event_group_id": str(ev["event_group_id"]),
                "candidate_opc": number(ev.get("candidate_opc")),
                "candidate_ocp": number(ev.get("candidate_ocp")),
                "candidate_centroid_distance_m": number(ev.get("candidate_centroid_distance_m")),
                "candidate_speed_ms": number(ev.get("candidate_speed_ms")),
                "candidate_heading_change_deg": number(ev.get("candidate_heading_change_deg")),
                "candidate_area_ratio": number(ev.get("candidate_area_ratio")),
                "candidate_final_cost": number(ev.get("candidate_final_cost")),
                "match_method": text(ev.get("match_method")),
            }
        )
    return rows


# ── track lifecycle ─────────────────────────────────────────────────────────


def _track_rows(
    tracked_cells: pd.DataFrame, events: list[dict], scan_iso: str, known: dict[str, dict]
) -> tuple[list[dict], dict[str, dict]]:
    """The lifecycle rows this scan touched, and every row that may still change."""
    initiated: dict[str, str] = {}  # uid → event group
    split_children: dict[str, tuple[str, str]] = {}  # child uid → (parent uid, group)
    terminated: dict[str, str] = {}  # uid → event group
    merged_into: dict[str, tuple[str, str]] = {}  # source uid → (survivor uid, group)
    for ev in events:
        etype, group = str(ev["event_type"]), str(ev["event_group_id"])
        source, target = ev.get("source_cell_uid"), ev.get("target_cell_uid")
        if etype == "INITIATION" and _present(target):
            initiated[str(target)] = group
        elif etype == "SPLIT" and _present(target) and _present(source):
            split_children[str(target)] = (str(source), group)
        elif etype == "TERMINATION" and _present(source):
            terminated[str(source)] = group
            if _present(target):  # a merged-away track ending: the target is the survivor
                merged_into[str(source)] = (str(target), group)
        elif etype == "MERGE" and _present(source) and _present(target):
            merged_into[str(source)] = (str(target), group)

    tracks = dict(known)
    touched: dict[str, dict] = {}
    for uid, area, reflectivity in zip(
        tracked_cells["cell_uid"],
        tracked_cells["area"],
        tracked_cells["max_reflectivity"],
        strict=True,
    ):
        uid, area, reflectivity = str(uid), float(area), float(reflectivity)
        if uid in tracks:
            row = tracks[uid]
            row = {
                **row,
                "last_seen_time": scan_iso,
                "n_scans": row["n_scans"] + 1,
                "duration_seconds": _age_seconds(scan_iso, row["first_seen_time"]),
                "max_area_sqkm": max(row["max_area_sqkm"] or 0.0, area),
                "max_reflectivity": max(row["max_reflectivity"] or 0.0, reflectivity),
            }
        else:
            origin: tuple[str, str | None, int, str | None]
            if uid in initiated:
                origin = ("INITIATION", initiated[uid], 0, None)
            elif uid in split_children:
                origin = ("SPLIT", split_children[uid][1], 1, split_children[uid][0])
            else:
                origin = ("UNKNOWN", None, 0, None)
            row = {
                "cell_uid": uid,
                "first_seen_time": scan_iso,
                "last_seen_time": scan_iso,
                "n_scans": 1,
                "origin_type": origin[0],
                "origin_event_group_id": origin[1],
                "origin_n_parents": origin[2],
                "origin_primary_parent_cell_uid": origin[3],
                "termination_type": "ACTIVE_AT_END",
                "termination_event_group_id": None,
                "terminated_into_cell_uid": None,
                "duration_seconds": 0.0,
                "max_area_sqkm": area,
                "max_reflectivity": reflectivity,
            }
        tracks[uid] = touched[uid] = row

    for uid, group in terminated.items():
        if uid in touched or uid not in tracks:
            continue
        if uid in merged_into:
            survivor, merge_group = merged_into[uid]
            ended = {
                "termination_type": "MERGED",
                "termination_event_group_id": merge_group,
                "terminated_into_cell_uid": survivor,
            }
        else:
            ended = {"termination_type": "TERMINATION", "termination_event_group_id": group}
        touched[uid] = {**tracks.pop(uid), **ended}
    return list(touched.values()), tracks
