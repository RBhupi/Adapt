# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Capture run provenance (git/host/user/python/platform/version), what each
module runs with, and config hashing.

Lives in the runtime composition layer: the orchestrator captures this once at
run start and hands it to ``ExecutionHistory`` via a ``RunStart`` DTO. Fields
that genuinely do not exist in the environment (e.g. no git checkout) are
recorded as ``None`` — the true value, never a fabricated default.
"""

from __future__ import annotations

import getpass
import hashlib
import platform
import socket
import subprocess
from pathlib import Path

from adapt import __version__
from adapt.configuration.schemas.internal import InternalConfig
from adapt.configuration.schemas.module_resolver import recorded_options
from adapt.contracts.execution_history import ModuleProvenance, RunProvenance
from adapt.utils.identity import options_hash

__all__ = ["capture_provenance", "config_hash", "module_provenance"]


# This file: the commit recorded is that of the checkout it is tracked in.
_SOURCE = Path(__file__).resolve()


def _git(*arguments: str) -> str | None:
    """What ``git <arguments>`` prints, run in the folder of Adapt's code; None
    when it fails or git is unavailable."""
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=_SOURCE.parent,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _git_commit() -> str | None:
    """Commit of the checkout Adapt's code runs from, followed by ``-dirty`` when
    the code has changed since that commit (a file of the package changed or
    added); None when Adapt does not run from a checkout (an installed release)
    or git is unavailable.

    Asked where the code is, not where the run was started (its store's folder);
    and only if that checkout tracks this file, so code installed into a folder
    inside some other checkout does not take that checkout's commit.
    """
    if _git("ls-files", "--error-unmatch", _SOURCE.name) is None:
        return None
    commit = _git("rev-parse", "HEAD")
    changed = _git("status", "--porcelain", "--", str(_SOURCE.parents[1]))
    if not commit or changed is None:
        return None
    return f"{commit}-dirty" if changed else commit


def capture_provenance() -> RunProvenance:
    """Snapshot the execution environment for reproducibility."""
    return RunProvenance(
        git_commit=_git_commit(),
        hostname=socket.gethostname(),
        username=getpass.getuser(),
        python_version=platform.python_version(),
        platform=platform.platform(),
        software_version=__version__,
    )


def module_provenance(config: InternalConfig) -> list[ModuleProvenance]:
    """What each module ``config`` lists runs with: its validated options,
    complete (``null`` for a module without options), and this Adapt's version."""
    ran = []
    for name in config.modules:
        options = recorded_options(config, name)
        options_json = "null" if options is None else options.model_dump_json()
        ran.append(
            ModuleProvenance(
                module=name,
                options_hash=options_hash(options_json),
                options_json=options_json,
                version=__version__,
            )
        )
    return ran


def config_hash(resolved_config_json: str) -> str:
    """Stable SHA-256 of the resolved configuration JSON (provenance fingerprint)."""
    return hashlib.sha256(resolved_config_json.encode("utf-8")).hexdigest()
