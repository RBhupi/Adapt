# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Core contract enforcement primitives.

ContractViolation and require are the only two names every contract
function depends on. This module has zero adapt imports so contracts
can be imported from anywhere without creating import cycles.
"""


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
