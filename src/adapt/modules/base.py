# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Base interface for all Adapt execution nodes.

Every node in the system — whether in execution/nodes/ or extensions/ — must
declare its name, inputs, outputs, and optionally input/output contracts.
The graph engine uses these declarations to build the execution DAG automatically.

Contract functions come from adapt.contracts — import them there, not here.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar


class BaseModule(ABC):
    """Abstract base for all Adapt execution nodes.

    Subclasses declare:
    - ``name``: unique identifier used in the execution graph
    - ``inputs``: list of context keys this node reads
    - ``outputs``: list of context keys this node writes
    - ``input_contracts``: optional {key: check_fn} validated before run()
    - ``output_contracts``: optional {key: check_fn} validated after run()

    The graph engine matches ``outputs`` of upstream nodes to ``inputs``
    of downstream nodes to resolve execution order automatically.
    Contract callables (from adapt.contracts) are invoked by GraphExecutor
    automatically — nodes do not call them manually.

    Example::

        from adapt.contracts import check_grid_ds_2d, check_segmented_ds

        class DetectModule(BaseModule):
            name = "detection"
            inputs = ["grid_ds_2d"]
            outputs = ["segmented_ds"]
            input_contracts  = {"grid_ds_2d": check_grid_ds_2d}
            output_contracts = {"segmented_ds": check_segmented_ds}

            def run(self, context):
                grid = context["grid_ds_2d"]
                cells = self._segmenter.segment(grid)
                return {"segmented_ds": cells}
    """

    name: ClassVar[str] = ""
    inputs: ClassVar[list[str]] = []
    outputs: ClassVar[list[str]] = []
    input_contracts: ClassVar[dict[str, Callable[[Any], None]]] = {}
    output_contracts: ClassVar[dict[str, Callable[[Any], None]]] = {}
    pipeline_phase: ClassVar[int] = 1

    @abstractmethod
    def run(self, context: dict) -> dict:
        """Execute this module.

        Parameters
        ----------
        context : dict
            Shared data store. Keys declared in ``inputs`` are guaranteed
            to be present (populated by upstream modules).

        Returns
        -------
        dict
            Keys declared in ``outputs``, populated by this module.
            The graph executor merges these into the shared context.
        """
        ...
