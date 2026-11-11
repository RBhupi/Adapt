# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The contract of what a module carries from one scan to the next.

A module keeps nothing between scans. What it needs next time it returns under
a key it also reads: the runtime hands the value back at the next scan (``None``
before the module has run) and saves it with the run's checkpoint, so a resumed
run continues exactly where it stopped. The value must therefore survive being
saved.
"""

import pickle
from typing import Any

from adapt.contracts.pipeline import require


def check_module_state(state: Any) -> None:
    """Bound contract for a carried state: present, and able to be checkpointed."""
    require(state is not None, "Module state contract violated: a module returned no state")
    try:
        pickle.dumps(state)
    except Exception as exc:
        require(False, f"Module state contract violated: it cannot be checkpointed ({exc})")
