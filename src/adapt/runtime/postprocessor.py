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
``register_store_input`` call — this file does not change. Every invocation
records per-module execution rows into ``run_modules`` when an observability
provider is injected (the CLI always injects one).
"""

import importlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.contracts import PersistenceMeta
from adapt.contracts.observability import Observability
from adapt.execution.graph.builder import GraphBuilder
from adapt.execution.graph.executor import GraphExecutor
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import resolve_enabled_modules
from adapt.modules.base import POSTPROCESS_PHASE
from adapt.persistence.execution_history import StoreExecutionHistory
from adapt.persistence.output_router import StoreOutputRouter
from adapt.runtime.observability import disabled_observability
from adapt.runtime.store_inputs import available_store_inputs, resolve_store_input

if TYPE_CHECKING:
    from adapt.configuration.schemas.internal import InternalConfig
    from adapt.persistence.store import Collection
    from adapt.persistence.store_registry import StoreRegistry

logger = logging.getLogger(__name__)

_POSTPROCESS_DEFAULTS_YAML = (
    Path(__file__).parent.parent / "configuration" / "postprocess_defaults.yaml"
)


def _ensure_postprocess_modules_registered(extensions: list[str] | None = None) -> None:
    """Import post-process module paths from postprocess_defaults.yaml + extensions.

    Importing each path triggers its ``registry.register()`` at module level.
    Mirrors the pipeline's ``_ensure_modules_registered`` (including its
    fail-loud rules); kept separate so the live pipeline never imports (and
    thus never sees) post-process modules.
    """
    try:
        with open(_POSTPROCESS_DEFAULTS_YAML, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except OSError as e:
        raise RuntimeError(
            f"Cannot read post-process module list '{_POSTPROCESS_DEFAULTS_YAML}': {e}"
        ) from e
    module_paths = cfg.get("postprocess", {}).get("modules", []) or []

    for path in module_paths:
        try:
            importlib.import_module(path)
            logger.debug("Registered post-process module from: %s", path)
        except Exception as e:
            raise ImportError(f"Failed to import post-process module '{path}': {e}") from e

    for path in extensions or []:
        try:
            importlib.import_module(path)
            logger.info("Registered post-process extension from: %s", path)
        except Exception as e:
            raise ImportError(f"Failed to load post-process extension '{path}': {e}") from e


class PostProcessor:
    """Run post-process (phase ``POSTPROCESS_PHASE``) modules over a stored run."""

    def __init__(
        self,
        collection: "Collection",
        registry: "StoreRegistry",
        run_id: str,
        config: "InternalConfig",
        extensions: list[str] | None = None,
        *,
        observability: Observability | None = None,
    ) -> None:
        self.collection = collection
        self.registry = registry
        self.run_id = run_id
        self.config = config
        self._extensions = extensions
        # Telemetry provider, injected by the CLI. Absent -> disabled (off), so
        # the rest of this class calls it unconditionally with no `if obs` branches.
        self._obs: Observability = (
            observability if observability is not None else disabled_observability()
        )

    def run(self, modules: list[str]) -> dict:
        """Resolve, execute, and persist the requested post-process modules.

        Parameters
        ----------
        modules : list[str]
            Names of post-process modules to run (from ``--module`` or the
            ``postprocess.modules`` config section).
        """
        _ensure_postprocess_modules_registered(self._extensions)

        available = [m for m in registry.create_modules() if m.pipeline_phase >= POSTPROCESS_PHASE]
        for name in modules:
            if name in registry and registry.get(name).pipeline_phase < POSTPROCESS_PHASE:
                raise ValueError(
                    f"Module '{name}' is not a post-process module "
                    f"(pipeline_phase={registry.get(name).pipeline_phase}); it runs in "
                    "the live pipeline ('adapt run-nexrad')."
                )
        selected = resolve_enabled_modules(available, modules=modules)
        logger.info("Post-processing modules: [%s]", ", ".join(m.name for m in selected))

        nodes = GraphBuilder(selected).build()
        context = self._build_context(selected)
        try:
            result = GraphExecutor(nodes, observability=self._obs).run(context)
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
        supplies. A declared input with no source fails here, by name — never
        as an opaque executor stall.
        """
        context: dict = {"run_id": self.run_id}
        context.update(resolve_module_configs(self.config))

        produced = {key for module in selected for key in module.outputs}
        needed = list(
            dict.fromkeys(
                key
                for module in selected
                for key in module.inputs
                if key not in context and key not in produced
            )
        )
        available = set(available_store_inputs())
        missing = [key for key in needed if key not in available]
        if missing:
            raise ValueError(
                f"No source for input(s) {missing} required by the selected "
                "post-process module(s): not produced by a selected module, not a "
                "module config, and no registered store-input provider. "
                f"Available store inputs: {', '.join(available_store_inputs())}"
            )
        for key in needed:
            context[key] = resolve_store_input(key, self.collection, self.registry, self.run_id)
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
