# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Core contract enforcement primitives.

ContractViolation and require are the only two names every contract
function depends on. This module has zero adapt imports so contracts
can be imported from anywhere without creating import cycles.
"""

from dataclasses import dataclass

# Keys the processor provides to every per-scan module; not produced by any module.
# ``wall_time`` is the machine's UTC time as the module that reads it starts: for
# a module that acts on the outside world (is this scan still fresh enough to
# act on?), never for a scientific output.
SCAN_SEEDS = frozenset(
    {"nexrad_file", "run_id", "scan_id", "scan_time", "wall_time", "prior_scans"}
)

# Inputs the runtime builds from the run's tables up to the scan, for a module
# that reads the run's history; no module produces them.
STORE_VIEWS = frozenset({"cells_so_far", "tracks_so_far"})


def config_key(module: str) -> str:
    """The context key under which the runtime provides ``module``'s config."""
    return f"{module}_config"


@dataclass(frozen=True)
class SharedConfig:
    """The values of a run that belong to no single module.

    Every module's ``configure`` is handed them; a module takes what it needs
    into its own config.
    """

    tracking_field: str  # the field the run detects, projects and tracks cells on
    z_level: float  # analysis altitude (m)
    max_scan_gap_minutes: float  # the largest gap between two scans any stage pairs across
    source: str  # where the scans come from; "stored_run" over a run the store already has


class ContractViolation(RuntimeError):
    """Raised when a pipeline contract is violated.

    This indicates a bug in pipeline logic, not bad user input or recoverable
    science edge cases. It means a pipeline stage did not produce the invariants
    it promised.

    Key distinction:
    - ValueError: User/config error (handled by Pydantic)
    - ContractViolation: Pipeline bug (programmer error)
    - Exception: Recoverable science issues (try/except in algorithms)
    """

    pass


def require(condition: bool, message: str) -> None:
    """Enforce a pipeline contract.

    Fail-fast: no recovery, no fallback, no silence.

    Raises
    ------
    ContractViolation
        If condition is False.
    """
    if not condition:
        raise ContractViolation(message)
