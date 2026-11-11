# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Base interface for all Adapt execution nodes.

Every node in the system — registered from the module catalog
(``configuration/defaults.yaml``) — must declare its name, inputs, outputs, and
optionally input/output contracts.
The graph engine uses these declarations to build the execution DAG automatically.

Contract functions come from adapt.contracts — import them there, not here.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar

from adapt.contracts import PersistenceSpec


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

    A module keeps nothing between scans. What it needs at its next scan it
    returns under a key it also lists in ``inputs``: the runtime hands the value
    back next time (``None`` before the module has run) and saves it with the
    run's checkpoint. What it needs of earlier scans' ordinary outputs it names
    in ``prior_inputs`` and reads from ``prior_scans``.
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
    summary: ClassVar[str] = ""  # one-line role, shown as a pipeline-file module comment
    inputs: ClassVar[list[str]] = []
    outputs: ClassVar[list[str]] = []
    input_contracts: ClassVar[dict[str, Callable[[Any], None]]] = {}
    output_contracts: ClassVar[dict[str, Callable[[Any], None]]] = {}
    required_history: ClassVar[int] = 1
    # The outputs of earlier scans this module reads from ``prior_scans``: the
    # runtime keeps exactly these (of every module) for the scans that follow.
    prior_inputs: ClassVar[tuple[str, ...]] = ()
    config_class: ClassVar[type | None] = None
    # How each output is persisted (see adapt.contracts.persistence). The
    # OutputRouter writes declared outputs mechanically; modules never touch
    # storage. Empty tuple = this module persists nothing directly.
    persistence: ClassVar[tuple[PersistenceSpec, ...]] = ()
    injected_global_fields: ClassVar[frozenset[str]] = frozenset()
    # The ParamConfig sections (pipeline-file ``params:`` keys) that hold this
    # module's options. A module without any owns its options instead, under
    # ``params: {<name>: ...}``.
    param_sections: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def module_summary(cls) -> str:
        """One-line role for the pipeline file's module-list comments.

        Prefers the explicit ``summary`` ClassVar, then the first non-empty line of
        the class docstring, falling back to the module ``name``.
        """
        if cls.summary:
            return cls.summary
        for line in (cls.__doc__ or "").strip().splitlines():
            if line.strip():
                return line.strip()
        return cls.name

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        """Owned, user-tunable defaults for this module.

        Sourced from ``config_class`` field defaults — the single place defaults
        live — excluding any field listed in ``injected_global_fields`` (those are
        filled from the shared global section by ``build_config``). Returns ``{}``
        when the module declares no ``config_class``.
        """
        if cls.config_class is None:
            return {}
        return {
            name: field.default
            for name, field in cls.config_class.model_fields.items()  # type: ignore[attr-defined]
            if name not in cls.injected_global_fields
        }

    @classmethod
    def options_text(cls) -> str | None:
        """This module's options as commented YAML to paste under ``params:``.

        None (the default) when ``default_params`` and the option descriptions
        say it all; a module whose options are a packaged preset returns it.
        """
        return None

    @classmethod
    def inputs_for(cls, config: Any) -> list[str]:
        """The context keys this module reads when configured with ``config``.

        ``inputs`` unless an option makes one unnecessary.
        """
        return cls.inputs

    @classmethod
    def build_config(cls, internal_config: Any) -> Any | None:
        """Build this module's frozen config by slicing the resolved InternalConfig.

        The module decides what it needs: global values, its own section, and
        (rarely) a cross-reference to another section. Returns None when the
        module needs no config. Modules override this with the slicing
        logic; the config engine calls it once per registered module at startup.
        """
        return None

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
