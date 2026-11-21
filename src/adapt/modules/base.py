# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Base interface for all Adapt modules.

Every module — a ``BaseModule`` subclass in ``modules/<name>/node.py``, named by
one line of the module catalog (``configuration/defaults.yaml``) — must declare
its name, inputs, outputs, and optionally input/output contracts.
The graph engine uses these declarations to build the execution DAG automatically.

Contract functions come from adapt.contracts — import them there, not here.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar

from pydantic import BaseModel

from adapt.contracts import PersistenceSpec, SharedConfig, StoreRead


class BaseModule(ABC):
    """Abstract base for all Adapt modules.

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
    in ``prior_inputs`` (``prior_inputs_for`` when an option decides) and reads
    from ``prior_scans``. The runtime keeps as many earlier scans as the listed
    modules need, and none when no module declares a need.

    A module owns its options: ``options_class`` is their model, the pipeline
    file sets them under ``params: {<name>: ...}``, and ``configure`` turns the
    validated options into the config ``run()`` receives.

    A module's rows are written over the stored ones key by key. Run again over
    a stored run with other options, it would leave rows of both options mixed,
    so that pass is refused — unless the module sets ``rows_keyed_by_options``:
    the key of every table it writes holds a hash of its options, so the rows
    of each set of options stand apart.

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
    # The module cannot run until ``required_history - 1`` earlier scans are
    # usable: it is skipped until then, with every module that reads from it,
    # and the runtime keeps at least that many. Scans it reads only when they
    # exist are not counted here (see ``prior_inputs``).
    required_history: ClassVar[int] = 1
    # The outputs of earlier scans this module reads from ``prior_scans``: the
    # runtime keeps exactly these (of every module, as ``prior_inputs_for`` its
    # options) for the scans that follow. A listed module must produce each one.
    # A module that names any is kept at least the last scan, when there is one.
    prior_inputs: ClassVar[tuple[str, ...]] = ()
    # The module's options: a frozen model that refuses unknown keys and gives
    # each option its default, bounds and description. None = no options.
    options_class: ClassVar[type[BaseModel] | None] = None
    # True when a pipeline file must give every option: ``options_preset`` is
    # then an example to copy, never laid under the file's options.
    every_option_written: ClassVar[bool] = False
    # True when the key of every table this module writes holds a hash of its
    # options: rows made with other options are other rows, never overwritten.
    rows_keyed_by_options: ClassVar[bool] = False
    # How each output is persisted (see adapt.contracts.persistence). The
    # OutputRouter writes declared outputs mechanically; modules never touch
    # storage. Empty tuple = this module persists nothing directly.
    persistence: ClassVar[tuple[PersistenceSpec, ...]] = ()
    # The rows of the run this module reads back from the store (see
    # adapt.contracts.StoreRead). The module lists each key in ``inputs``; the
    # runtime fills it once the listed modules that write those rows have
    # stored this scan. Empty tuple = this module reads no stored rows.
    store_reads: ClassVar[tuple[StoreRead, ...]] = ()

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
    def options_preset(cls) -> str | None:
        """This module's default options as packaged, commented YAML: ``<name>: {...}``.

        None (the default) when the defaults of ``options_class`` are the
        module's defaults; a module whose options are a packaged rule returns it.
        With ``every_option_written`` it is an example of every option, not a
        default.
        """
        return None

    @classmethod
    def configure(cls, options: Any, shared: SharedConfig) -> Any:
        """The frozen config ``run()`` receives, built once when the run is configured.

        ``options`` are this module's validated options (an ``options_class``
        instance; None without one) and ``shared`` the values of the whole run.
        By default the config is the options; a module overrides this to add
        what it takes from ``shared`` or derives, or to refuse (``ValueError``)
        a run it cannot make.
        """
        return options

    @classmethod
    def inputs_for(cls, config: Any) -> list[str]:
        """The context keys this module reads when configured with ``config``.

        ``inputs`` unless an option makes one unnecessary.
        """
        return cls.inputs

    @classmethod
    def prior_inputs_for(cls, config: Any) -> tuple[str, ...]:
        """The outputs of earlier scans this module reads when configured with ``config``.

        ``prior_inputs`` unless an option makes one unnecessary.
        """
        return cls.prior_inputs

    @classmethod
    def method_line(cls, config: Any) -> str | None:
        """This module's line of the run header, read off its own built ``config``.

        One short "<method> · <key choices>", never the full options. None (the
        default) for a module with no method to name: it gets no line.
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
