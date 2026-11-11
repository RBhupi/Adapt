# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""A run's checkpoint object: what the run needs to go on after its last complete scan.

Only the latest is kept. The bytes are opaque here (the runtime writes and
reads them); this module places them in the collection's objects, atomically,
as ``<run_id>/checkpoint/<scan_id>.ckpt``. Which one is current is the
catalog's ``run_checkpoints`` row, written with the scan's completion.
"""

import os
from pathlib import Path

from adapt.persistence.errors import StoreError
from adapt.persistence.store import Collection

__all__ = ["discard_other_checkpoints", "read_checkpoint", "write_checkpoint"]

_SUFFIX = ".ckpt"


def _folder(collection: Collection, run_id: str) -> Path:
    return collection.objects_dir / run_id / "checkpoint"


def write_checkpoint(collection: Collection, run_id: str, scan_id: str, payload: bytes) -> str:
    """Store ``payload`` as the checkpoint after scan ``scan_id``; return its object name.

    The file appears complete or not at all. It is not current until the
    catalog names it.
    """
    folder = _folder(collection, run_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{scan_id}{_SUFFIX}"
    staging = path.with_suffix(".writing")
    with open(staging, "wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(staging, path)
    return path.relative_to(collection.objects_dir).as_posix()


def read_checkpoint(collection: Collection, object_name: str) -> bytes:
    """The bytes of checkpoint ``object_name``."""
    path = collection.objects_dir / object_name
    if not path.is_file():
        raise StoreError(f"Checkpoint object '{object_name}' is cataloged but missing: {path}")
    return path.read_bytes()


def discard_other_checkpoints(collection: Collection, run_id: str, keep: str) -> None:
    """Remove every checkpoint object of the run but ``keep`` (the current one)."""
    folder = _folder(collection, run_id)
    current = collection.objects_dir / keep
    for path in folder.iterdir():
        if path != current:
            path.unlink()
