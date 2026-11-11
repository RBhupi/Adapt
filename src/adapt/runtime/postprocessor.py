# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Out-of-band store enrichment by composing the live pipeline's infra.

``PostProcessor`` enriches an already-processed run with *extension* tables
(e.g. lightning stats), without touching the proven ``RadarProcessor``. It is
an after-the-fact module execution: outputs attach to the ORIGINAL run_id, and
a re-run replaces the module's own tables (never anyone else's).

It reuses — by composition, not inheritance — the same components the pipeline
uses: the module ``registry``, ``GraphBuilder``/``GraphExecutor`` for
dependency resolution, ``resolve_module_configs`` for config, and the
``StoreOutputRouter`` for persistence (whose frozen-schema writer enforces
module table ownership). Post-process modules declare
``pipeline_phase = POSTPROCESS_PHASE`` and are selected exclusively here; the
live pipeline rejects them structurally.

Store-backed inputs (mask geometry, collection location, ...) come from the
``adapt.runtime.store_inputs`` provider registry: a module may declare any
registered input key, and adding a new key is one provider function plus one
``register_store_input`` call — this file does not change. A declared input
with no source is refused when the graph is built (``postprocess_graph``). Every invocation
records per-module execution rows into ``run_modules`` when an observability
provider is injected (``postprocess_run`` always injects one).

``postprocess_run`` is the composition root for one post-processing pass over a
stored run: it opens the store, reloads the run's stored config, wires
observability with real clocks, and owns logging for the call.
"""

import logging
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from adapt.configuration.schemas.internal import InternalConfig
from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.contracts import PersistenceMeta, config_key
from adapt.contracts.observability import Observability
from adapt.execution.graph.builder import GraphBuilder
from adapt.execution.graph.executor import GraphExecutor
from adapt.execution.graph.node import Node
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import (
    _ensure_postprocess_modules_registered,
    resolve_enabled_modules,
)
from adapt.modules.base import POSTPROCESS_PHASE
from adapt.persistence.execution_history import StoreExecutionHistory
from adapt.persistence.output_router import StoreOutputRouter
from adapt.persistence.store import Store
from adapt.persistence.store_registry import StoreRegistry
from adapt.runtime.logging_setup import configure_logging, shutdown_logging
from adapt.runtime.observability import build_observability, disabled_observability, obs_settings
from adapt.runtime.store_inputs import available_store_inputs, resolve_store_input

if TYPE_CHECKING:
    from adapt.persistence.store import Collection

__all__ = ["PostProcessor", "postprocess_graph", "postprocess_run"]

logger = logging.getLogger(__name__)


def postprocess_run(
    store_root: str | Path,
    run_id: str,
    modules: list[str],
    module_params: dict[str, dict[str, Any]],
) -> None:
    """Run post-process ``modules`` over the stored run ``run_id`` of a store.

    The run's own stored config is the base; ``module_params`` is overlaid on its
    ``module_params`` per module (given keys win, the stored rest is kept).
    Logging is configured from that config for the duration of the call —
    file ``logs/postprocess_{collection}.log`` in the store — and shut down at
    the end, success or failure.

    Raises
    ------
    StoreError
        ``store_root`` is not an initialized store, or ``run_id`` is not a run in it.
    """
    store = Store.open(store_root)
    run_registry = StoreRegistry.get_instance(store.root)
    try:
        run = run_registry.get_run(run_id)
        collection = store.collection(run["collection_id"])
        stored = InternalConfig.model_validate_json(run["config_json"])
        config = stored.with_module_params(module_params)

        settings = obs_settings(config.logging)
        log_path = store.root / "logs" / f"postprocess_{collection.collection_id}.log"
        configure_logging(settings, log_path)
        try:
            # Real clocks/rng are legal only at composition roots: post-hoc
            # executions record run_modules audit rows like the live pipeline.
            observability = build_observability(
                settings,
                clock=time.perf_counter,
                wall_clock=lambda: datetime.now(UTC),
                rng=random.Random(),
            )
            PostProcessor(
                collection, run_registry, run_id, config, observability=observability
            ).run(modules)
        finally:
            shutdown_logging()
    finally:
        store.close()
        run_registry.close()


def postprocess_graph(modules: list) -> list[Node]:
    """The graph of the post-process ``modules``, as ``PostProcessor`` runs it.

    Raises
    ------
    ValueError
        A module input is not another module's output, ``run_id``, a module
        config, or a registered store input; the message names the keys and
        lists what is provided.
    """
    provided = frozenset(
        {"run_id", *(config_key(m.name) for m in modules), *available_store_inputs()}
    )
    return GraphBuilder(modules, provided=provided).build()


class PostProcessor:
    """Run post-process (phase ``POSTPROCESS_PHASE``) modules over a stored run."""

    def __init__(
        self,
        collection: "Collection",
        registry: "StoreRegistry",
        run_id: str,
        config: "InternalConfig",
        *,
        observability: Observability | None = None,
    ) -> None:
        self.collection = collection
        self.registry = registry
        self.run_id = run_id
        self.config = config
        # Telemetry provider, injected by postprocess_run. Absent -> disabled (off), so
        # the rest of this class calls it unconditionally with no `if obs` branches.
        self._obs: Observability = (
            observability if observability is not None else disabled_observability()
        )

    def run(self, modules: list[str]) -> dict:
        """Resolve, execute, and persist the requested post-process modules.

        Parameters
        ----------
        modules : list[str]
            Names of post-process modules to run (the pipeline file's
            ``postprocessing:`` list).
        """
        _ensure_postprocess_modules_registered()

        available = [m for m in registry.create_modules() if m.pipeline_phase >= POSTPROCESS_PHASE]
        for name in modules:
            if name in registry and registry.get(name).pipeline_phase < POSTPROCESS_PHASE:
                raise ValueError(
                    f"Module '{name}' is not a post-process module "
                    f"(pipeline_phase={registry.get(name).pipeline_phase}); it runs in "
                    "the live pipeline (list it under 'core:' or 'extended:')."
                )
        selected = resolve_enabled_modules(available, modules=modules)
        logger.info("Post-processing modules: [%s]", ", ".join(m.name for m in selected))

        nodes = postprocess_graph(selected)
        context = self._build_context(selected)
        try:
            result = GraphExecutor(nodes, observability=self._obs).run(context, history=0).context
        finally:
            self._record_history()

        self._persist(selected, result)
        return result

    def _record_history(self) -> None:
        """Upsert this invocation's module spans into run_modules (post-hoc audit).

        Runs in a finally: a failed module still leaves its 'failed' row before
        the exception propagates. The record is run-level — the run_modules
        schema keys on (run_id, module), so scan_id is passed empty.
        """
        spans = self._obs.drain_spans()
        if not spans:
            return
        StoreExecutionHistory(self.registry).record_modules(
            self.run_id, scan_id="", spans=spans, recorded_at=datetime.now(UTC)
        )

    def _build_context(self, selected: list) -> dict:
        """Run-level base context, module configs, and on-demand store inputs.

        Store-backed inputs come from the ``adapt.runtime.store_inputs``
        provider registry and are materialized only for keys a selected module
        declares that neither the base context nor a sibling selected module
        supplies.
        """
        context: dict = {"run_id": self.run_id}
        context.update(resolve_module_configs(self.config, [m.name for m in selected]))

        produced = {key for module in selected for key in module.outputs}
        for module in selected:
            for key in module.inputs:
                if key not in context and key not in produced:
                    context[key] = resolve_store_input(
                        key, self.collection, self.registry, self.run_id
                    )
        return context

    def _persist(self, modules: list, result: dict) -> None:
        """Route each module's declared persistence specs through the OutputRouter.

        Run-level persist: there is no single scan_time (rows carry their own),
        so ``meta.scan_time`` is None and any time-stamped artifact spec would
        correctly raise.
        """
        meta = PersistenceMeta(
            scan_time=None,
            scan_id=None,
            run_id=self.run_id,
            source_file="",
            collection_id=self.collection.collection_id,
        )
        StoreOutputRouter(self.collection).persist(modules, result, meta)
