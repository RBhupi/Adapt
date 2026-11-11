# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Object store for one collection's ``objects/`` directory.

Layout — names are readable, identity is not in the name::

    objects/raw/<original filename>                         a downloaded scan, as it came
    objects/<run_id>/<module>/<source stem>.<product>.nc    what a module wrote for a scan

``raw_object_name`` and ``product_object_name`` are the only places that spell
those names. Identity and provenance stay in the catalog: the uuid4
``artifact_id``, the sha256 checksum and the lineage edges.

Write protocol: :meth:`ObjectStore.begin` allocates the artifact_id BEFORE any
bytes exist and hands back a staging path inside store-owned
``objects/.staging/``. Nothing is visible or cataloged until :meth:`commit`,
which checksums the staged bytes, atomically publishes them via ``os.replace``,
and registers the full catalog row. :meth:`abort` removes the staging file and
leaves no trace.

A name is taken once (letter case ignored, as filesystems may). Only its
owner — the same run, producer, artifact type and scan — may rewrite it, and
only by asking (``commit(..., replace=True)``): the bytes are replaced
atomically and the catalog row keeps its artifact_id. A raw object is never
replaced.
"""

import hashlib
import logging
import os
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from adapt.persistence.collection_catalog import ArtifactRecord, Catalog
from adapt.persistence.errors import StoreError
from adapt.utils.time import to_scan_iso

__all__ = [
    "ArtifactMeta",
    "ObjectStore",
    "ObjectWriteHandle",
    "product_object_name",
    "raw_object_name",
]

logger = logging.getLogger(__name__)

_HASH_CHUNK_BYTES = 1 << 20
_STAGING = ".staging"
_RAW_VOLUME = "raw_volume"


def raw_object_name(filename: str) -> str:
    """Object name of a downloaded source file: its original name, under ``raw/``."""
    return f"raw/{filename}"


def product_object_name(run_id: str, module: str, source_filename: str, product_type: str) -> str:
    """Object name of the NetCDF ``module`` wrote in ``run_id`` for one source file."""
    return f"{run_id}/{module}/{Path(source_filename).stem}.{product_type}.nc"


@dataclass(frozen=True)
class ArtifactMeta:
    """Caller-supplied metadata for one object; the store computes the rest."""

    artifact_type: str
    producer: str
    run_id: str
    scan_id: str | None
    observation_time: datetime | None
    original_filename: str | None = None
    source_uri: str | None = None


@dataclass(frozen=True)
class ObjectWriteHandle:
    """One in-flight object write: id allocated, bytes staged, not yet visible."""

    artifact_id: str
    object_name: str
    staging_path: Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _checked_name(object_name: str) -> PurePosixPath:
    """``object_name`` as a path that stays inside ``objects/``, or raise.

    The name must already be in its one canonical spelling: the catalog keys
    on the string, so ``raw/./x`` or ``raw//x`` would be a second name for the
    file ``raw/x``.
    """
    path = PurePosixPath(object_name)
    if (
        str(path) != object_name
        or object_name == "."
        or path.is_absolute()
        or ".." in path.parts
        or path.parts[0].lower() == _STAGING
    ):
        raise StoreError(
            f"Invalid object name '{object_name}': it must be a plain relative file path "
            f"inside objects/ (no '.', '..' or '//', not absolute, not under {_STAGING}/)"
        )
    return path


class ObjectStore:
    """Atomic, catalog-registered writes into one collection's ``objects/``."""

    def __init__(self, objects_dir: str | Path, catalog: Catalog) -> None:
        self.objects_dir = Path(objects_dir)
        self._catalog = catalog
        self._staging_dir = self.objects_dir / _STAGING

    def begin(self, object_name: str) -> ObjectWriteHandle:
        """Allocate an artifact_id and staging path for ``object_name``; write, then commit."""
        suffix = _checked_name(object_name).suffix
        self._staging_dir.mkdir(exist_ok=True)
        artifact_id = uuid.uuid4().hex
        return ObjectWriteHandle(
            artifact_id=artifact_id,
            object_name=object_name,
            staging_path=self._staging_dir / f"{artifact_id}{suffix}",
        )

    def commit(
        self,
        handle: ObjectWriteHandle,
        meta: ArtifactMeta,
        parents: Sequence[str] = (),
        *,
        replace: bool = False,
    ) -> ArtifactRecord:
        """Publish the staged bytes atomically, register the row and lineage edges.

        ``replace`` lets the owner of an existing object rewrite it in place: the
        row keeps its artifact_id and takes the new checksum, size and time.

        Raises
        ------
        StoreError
            Nothing is staged; a lineage parent is not cataloged; or the name is
            taken and this is not its owner asking to replace it.
        """
        if not handle.staging_path.exists():
            raise StoreError(
                f"No staged object for artifact '{handle.artifact_id}' "
                f"(missing {handle.staging_path}); begin/write before commit, "
                "and commit only once"
            )
        for parent in parents:
            if self._catalog.get_artifact(parent) is None:
                raise StoreError(
                    f"Cannot commit '{handle.object_name}': lineage parent "
                    f"'{parent}' is not cataloged"
                )

        existing = self._catalog.find_by_object_name(handle.object_name)
        if existing is not None:
            self._require_owner_replacing(existing, meta, replace)
            return self._rewrite(handle, existing, parents)

        final_path = self.objects_dir / handle.object_name
        if final_path.exists():
            # Published but never cataloged: a commit interrupted between the
            # two steps. Nothing references it, and it would block every retry.
            logger.warning("Overwriting uncataloged object '%s'", handle.object_name)
        record = ArtifactRecord(
            artifact_id=handle.artifact_id,
            artifact_type=meta.artifact_type,
            producer=meta.producer,
            run_id=meta.run_id,
            scan_id=meta.scan_id,
            observation_time=(
                to_scan_iso(meta.observation_time) if meta.observation_time is not None else None
            ),
            original_filename=meta.original_filename,
            source_uri=meta.source_uri,
            object_name=handle.object_name,
            checksum_sha256=_sha256_file(handle.staging_path),
            size_bytes=handle.staging_path.stat().st_size,
        )
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(handle.staging_path, final_path)
        self._catalog.register_artifact(record)
        for parent in parents:
            self._catalog.add_lineage(record.artifact_id, parent)
        return record

    @staticmethod
    def _require_owner_replacing(existing: dict, meta: ArtifactMeta, replace: bool) -> None:
        """Raise unless ``meta`` is the owner of ``existing`` asking to replace it."""
        name = existing["object_name"]
        if existing["artifact_type"] == _RAW_VOLUME:
            raise StoreError(
                f"Object '{name}' already exists (artifact '{existing['artifact_id']}'); "
                "a raw object is never replaced"
            )
        owner = (
            existing["run_id"],
            existing["producer"],
            existing["artifact_type"],
            existing["scan_id"],
        )
        if not replace or owner != (
            meta.run_id,
            meta.producer,
            meta.artifact_type,
            meta.scan_id,
        ):
            raise StoreError(
                f"Object '{name}' already exists (artifact '{existing['artifact_id']}': "
                f"{existing['artifact_type']} of scan {existing['scan_id']}, written by "
                f"{existing['producer']} in run {existing['run_id']}); only its owner may "
                f"replace it, and {meta.producer} in run {meta.run_id} asked to "
                f"{'replace' if replace else 'write'} it as {meta.artifact_type} of scan "
                f"{meta.scan_id}"
            )

    def _rewrite(
        self, handle: ObjectWriteHandle, existing: dict, parents: Sequence[str]
    ) -> ArtifactRecord:
        """Replace the bytes of ``existing`` in place; its row keeps its identity."""
        checksum = _sha256_file(handle.staging_path)
        size = handle.staging_path.stat().st_size
        os.replace(handle.staging_path, self.objects_dir / existing["object_name"])
        self._catalog.update_artifact(existing["artifact_id"], checksum, size)
        known = set(self._catalog.parents_of(existing["artifact_id"]))
        for parent in parents:
            if parent not in known:
                self._catalog.add_lineage(existing["artifact_id"], parent)
        return ArtifactRecord(
            artifact_id=existing["artifact_id"],
            artifact_type=existing["artifact_type"],
            producer=existing["producer"],
            run_id=existing["run_id"],
            scan_id=existing["scan_id"],
            observation_time=existing["observation_time"],
            original_filename=existing["original_filename"],
            source_uri=existing["source_uri"],
            object_name=existing["object_name"],
            checksum_sha256=checksum,
            size_bytes=size,
        )

    def abort(self, handle: ObjectWriteHandle) -> None:
        """Discard the staged bytes; nothing was published or cataloged."""
        handle.staging_path.unlink(missing_ok=True)
