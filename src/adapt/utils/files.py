# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Atomic text writes and fail-loud YAML section reads for file-driven consumers."""

import os
import tempfile
import time
from pathlib import Path

import yaml

# Windows refuses to replace a file another handle holds open (PermissionError)
# until that handle closes; readers and antivirus scans hold one for moments.
_REPLACE_ATTEMPTS = 20
_REPLACE_RETRY_SECONDS = 0.1


def atomic_write_text(path: str | Path, text: str) -> None:
    """Replace ``path`` with ``text`` (UTF-8, ``\\n`` newlines) in one atomic rename.

    Readers see either the old file or the complete new one, never a partial
    write. A PermissionError from the rename is retried for about two seconds,
    then re-raised. On any failure the temp file is removed and the error
    re-raised. The file is world-readable (0644): published files carry no
    credential and are read by other users' processes.
    """
    target = Path(path)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.chmod(tmp_path, 0o644)  # mkstemp creates 0o600; published files are world-readable
        _replace(tmp_path, target)
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def _replace(src: Path, dst: Path) -> None:
    for _ in range(_REPLACE_ATTEMPTS - 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            time.sleep(_REPLACE_RETRY_SECONDS)
    os.replace(src, dst)


def read_yaml_section(path: str | Path, key: str) -> dict:
    """Return the top-level ``key`` mapping of the YAML file at ``path``.

    Sibling sections are ignored: each component validates only its own.
    Raises ValueError when the document or the section is not a mapping, or
    when ``key`` is missing.
    """
    with open(path, encoding="utf-8") as fh:
        document = yaml.safe_load(fh)
    if not isinstance(document, dict):
        raise ValueError(f"{path}: document is not a mapping (got {type(document).__name__})")
    if key not in document:
        raise ValueError(f"{path}: missing section '{key}' (present: {sorted(document)})")
    section = document[key]
    if not isinstance(section, dict):
        raise ValueError(f"{path}: section '{key}' is not a mapping (got {type(section).__name__})")
    return section
