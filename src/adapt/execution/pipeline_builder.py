# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Module discovery (the module catalog) and selection for pipeline assembly.

The catalog is two discovery files of dotted import paths:
``configuration/defaults.yaml`` (the live pipeline) and
``configuration/postprocess_defaults.yaml`` (post-processing). Importing a path
triggers its module's ``registry.register()`` call. The live pipeline imports
only the first file, the PostProcessor only the second; the pipeline-file schema
reads both through ``catalog_module_names`` to validate module names.

``resolve_enabled_modules`` filters the registered set down to a run's
allowlist, and ``scan_graph`` wires that selection into the one per-scan graph.
Everything fails loudly: an unreadable or empty module list, or a
module that cannot be imported, aborts startup — there is no fallback pipeline.
"""

import importlib
import logging
from pathlib import Path

import yaml

from adapt.contracts import SCAN_SEEDS, config_key
from adapt.execution.graph.builder import GraphBuilder, data_edges
from adapt.execution.graph.node import Node
from adapt.execution.module_registry import registry
from adapt.modules.base import EXTENDED_PHASE

logger = logging.getLogger(__name__)

_CATALOG_DIR = Path(__file__).parent.parent / "configuration"
_DEFAULTS_YAML = _CATALOG_DIR / "defaults.yaml"
_POSTPROCESS_DEFAULTS_YAML = _CATALOG_DIR / "postprocess_defaults.yaml"


def _import_catalog(path: Path, section: str, label: str) -> list[str]:
    """Import every dotted path under ``<section>.modules`` of ``path``; return them."""
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except OSError as e:
        raise RuntimeError(f"Cannot read {label} module list '{path}': {e}") from e

    module_paths = (cfg or {}).get(section, {}).get("modules", [])
    if not module_paths:
        raise RuntimeError(f"No {label} modules declared under '{section}.modules' in '{path}'")

    for module_path in module_paths:
        try:
            importlib.import_module(module_path)
            logger.debug("Registered %s module from: %s", label, module_path)
        except Exception as e:
            raise ImportError(f"Failed to import {label} module '{module_path}': {e}") from e
    return list(module_paths)


def _ensure_modules_registered() -> None:
    """Import the live pipeline modules listed in configuration/defaults.yaml.

    To add a module: add its dotted import path to defaults.yaml.

    Raises
    ------
    RuntimeError
        If defaults.yaml is unreadable or declares no modules.
    ImportError
        If any declared module fails to import.
    """
    _import_catalog(_DEFAULTS_YAML, "pipeline", "pipeline")


def _ensure_postprocess_modules_registered() -> None:
    """Import the post-process modules listed in configuration/postprocess_defaults.yaml.

    Kept separate from ``_ensure_modules_registered`` so the live pipeline never
    imports post-process modules. Same fail-loud rules.
    """
    _import_catalog(_POSTPROCESS_DEFAULTS_YAML, "postprocess", "post-process")


def catalog_module_names() -> list[str]:
    """Names of every catalog module: defaults.yaml first, then postprocess_defaults.yaml.

    Each file keeps its own order. Registers every catalog module as a side
    effect (importing is how a module registers).

    Raises
    ------
    RuntimeError
        If a discovery file is unreadable or empty, or one of its paths
        registers no module.
    """
    paths = [
        *_import_catalog(_DEFAULTS_YAML, "pipeline", "pipeline"),
        *_import_catalog(_POSTPROCESS_DEFAULTS_YAML, "postprocess", "post-process"),
    ]
    names: list[str] = []
    for path in paths:
        found = [n for n in registry.list_modules() if registry.get(n).__module__ == path]
        if not found:
            raise RuntimeError(f"Catalog entry '{path}' registers no module")
        names.extend(found)
    return names


def resolve_enabled_modules(all_modules: list, modules: list[str] | None = None) -> list:
    """Filter ``all_modules`` to the ``modules`` allowlist, preserving order.

    ``None`` enables every module. Every listed name must be a real module.
    After filtering, validates that no enabled module needs an input produced
    solely by a module that is not listed — raising a clear error rather than failing
    opaquely at runtime.

    Parameters
    ----------
    all_modules : list
        All registered module instances (each with ``name``/``inputs``/``outputs``).
    modules : list[str], optional
        The run's module allowlist.
    """
    by_name = {m.name: m for m in all_modules}
    for n in modules or []:
        if n not in by_name:
            raise ValueError(f"Unknown module '{n}' in modules. Available: {', '.join(by_name)}")

    enabled = set(by_name) if modules is None else set(modules)

    producer: dict[str, str] = {}
    for m in all_modules:
        for out in m.outputs:
            producer[out] = m.name
    for m in all_modules:
        if m.name not in enabled:
            continue
        for inp in m.inputs:
            src = producer.get(inp)
            if src is not None and src not in enabled:
                raise ValueError(
                    f"Module '{m.name}' needs input '{inp}', which module '{src}' "
                    f"produces — list '{src}' too, or remove '{m.name}'"
                )

    return [m for m in all_modules if m.name in enabled]


def scan_graph(modules: list) -> list[Node]:
    """The one per-scan graph of ``modules`` (core and extended), as the processor runs it.

    The runtime provides ``SCAN_SEEDS`` and one config per module; every other
    input must be another listed module's output. Extended modules run after
    the scan's core outputs are stored, so a core module cannot read one's output.

    Raises
    ------
    ValueError
        Two modules declare one output key, an input has no source, or a core
        module reads an extended module's output.
    """
    provided = SCAN_SEEDS | {config_key(m.name) for m in modules}
    nodes = GraphBuilder(modules, provided=provided).build()
    extended = {m.name for m in modules if m.pipeline_phase >= EXTENDED_PHASE}
    for producer, consumer, key in data_edges(modules):
        if producer in extended and consumer not in extended:
            raise ValueError(
                f"Core module '{consumer}' needs input '{key}' from extended module "
                f"'{producer}', which runs only after the scan's core outputs are stored"
            )
    return nodes
