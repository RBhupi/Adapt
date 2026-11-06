# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The target selection engine's current-selection file, as radar command reads it.

The file is the cross-consumer contract: radar command never imports the
target selection package, only parses what its writer produces.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from adapt.utils.time import from_scan_iso


@dataclass(frozen=True)
class SelectionRef:
    """Which cell was selected at which scan; ``cell_uid`` is None when none was."""

    collection: str
    run_id: str
    scan_id: str
    scan_time: datetime
    cell_uid: str | None


# Exactly the keys write_current_selection emits, with their JSON types.
_TYPES: dict[str, tuple[type, ...]] = {
    "collection": (str,),
    "run_id": (str,),
    "scan_id": (str,),
    "scan_time": (str,),
    "cell_uid": (str, type(None)),
    "reason": (str, type(None)),
    "score": (int, float, type(None)),
}


def read_selection(path: str | Path) -> SelectionRef:
    """Parse the current-selection JSON at ``path``; ValueError names any bad key."""
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [key for key in _TYPES if key not in obj]
    if missing:
        raise ValueError(f"{path}: selection is missing key(s) {missing}")
    extra = sorted(set(obj) - set(_TYPES))
    if extra:
        raise ValueError(f"{path}: selection has unexpected key(s) {extra}")
    for key, types in _TYPES.items():
        if not isinstance(obj[key], types):
            raise ValueError(
                f"{path}: selection key '{key}' has type {type(obj[key]).__name__}, "
                f"expected {' or '.join(t.__name__ for t in types)}"
            )
    try:
        scan_time = from_scan_iso(obj["scan_time"])
    except ValueError as exc:
        raise ValueError(
            f"{path}: selection key 'scan_time' is not a UTC scan time: {exc}"
        ) from exc
    return SelectionRef(
        collection=obj["collection"],
        run_id=obj["run_id"],
        scan_id=obj["scan_id"],
        scan_time=scan_time,
        cell_uid=obj["cell_uid"],
    )
