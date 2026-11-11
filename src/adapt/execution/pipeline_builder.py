# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Module discovery (the module catalog) and selection for pipeline assembly.

The catalog is one discovery file of dotted import paths,
``configuration/defaults.yaml``. Importing a path triggers its module's
``registry.register()`` call.

``resolve_enabled_modules`` filters the registered set down to a run's list,
and ``scan_graph`` wires that selection into the one per-scan graph.
Everything fails loudly: an unreadable or empty module list, or a
module that cannot be imported, aborts startup — there is no fallback pipeline.
"""

import importlib
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from adapt.contracts import (
    SCAN_SEEDS,
    STORE_VIEWS,
    NetcdfArtifact,
    ProductTableWrite,
    config_key,
)
from adapt.execution.graph.builder import GraphBuilder
from adapt.execution.graph.node import Node
from adapt.execution.module_registry import registry

logger = logging.getLogger(__name__)

_DEFAULTS_YAML = Path(__file__).parent.parent / "configuration" / "defaults.yaml"


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
    """Import the modules listed in configuration/defaults.yaml.

    To add a module: add its dotted import path to defaults.yaml.

    Raises
    ------
    RuntimeError
        If defaults.yaml is unreadable or declares no modules.
    ImportError
        If any declared module fails to import.
    """
    _import_catalog(_DEFAULTS_YAML, "pipeline", "pipeline")


def catalog_module_names() -> list[str]:
    """Names of every catalog module, in the catalog's order.

    Registers every catalog module as a side effect (importing is how a module
    registers).

    Raises
    ------
    RuntimeError
        If the discovery file is unreadable or empty, or one of its paths
        registers no module.
    """
    names: list[str] = []
    for path in _import_catalog(_DEFAULTS_YAML, "pipeline", "pipeline"):
        found = [n for n in registry.list_modules() if registry.get(n).__module__ == path]
        if not found:
            raise RuntimeError(f"Catalog entry '{path}' registers no module")
        names.extend(found)
    return names


def resolve_enabled_modules(all_modules: list, modules: list[str] | None = None) -> list:
    """Filter ``all_modules`` to the ``modules`` list, in ``all_modules``' order.

    ``None`` enables every module. Every listed name must be a real module.

    Parameters
    ----------
    all_modules : list
        All registered module instances (each with ``name``/``inputs``/``outputs``).
    modules : list[str], optional
        The run's module list.
    """
    by_name = {m.name: m for m in all_modules}
    for n in modules or []:
        if n not in by_name:
            raise ValueError(f"Unknown module '{n}' in modules. Available: {', '.join(by_name)}")
    enabled = set(by_name) if modules is None else set(modules)
    return [m for m in all_modules if m.name in enabled]


def stored_output_keys() -> frozenset[str]:
    """Output keys of the registered modules that are stored as an artifact or a
    table, and so can be read back from the store."""
    return frozenset(
        spec.key
        for name in registry.list_modules()
        for spec in registry.get(name).persistence
        if isinstance(spec, NetcdfArtifact | ProductTableWrite)
    )


def carried_keys(modules: list) -> frozenset[str]:
    """The keys the modules carry from one scan to the next: each both read and
    written by one module, whose last value the runtime hands back to it."""
    return frozenset(key for module in modules for key in module.inputs if key in module.outputs)


def scan_graph(
    modules: list, configs: Mapping[str, Any] | None = None, *, stored_run: bool = False
) -> list[Node]:
    """The one per-scan graph of ``modules``, as the processor runs it.

    The runtime provides ``SCAN_SEEDS``, one config per module, the
    ``STORE_VIEWS`` and each module's own carried state (``carried_keys``); every
    other input must be another listed module's output.
    Over a stored run (``stored_run``) an input may also be any output a
    registered module stores: the runtime reads it back, scan by scan. With
    ``configs`` (each module's built config, by ``config_key``), a module reads
    what its options make it read (``inputs_for``); without, what it declares.

    Raises
    ------
    ValueError
        Two modules declare one output key, or an input has no source — naming
        the module that produces it, when one is registered but not listed.
    """
    if configs is not None:
        for module in modules:
            key = config_key(module.name)
            if key in configs:
                module.inputs = type(module).inputs_for(configs[key])
    provided = SCAN_SEEDS | STORE_VIEWS | {config_key(m.name) for m in modules}
    provided |= carried_keys(modules)
    if stored_run:
        provided |= stored_output_keys()
    produced = {key for module in modules for key in module.outputs}
    elsewhere = {
        key: name
        for name in registry.list_modules()
        for key in registry.get(name).outputs
        if key not in produced
    }
    for module in modules:
        for key in module.inputs:
            if key not in produced and key not in provided and key in elsewhere:
                raise ValueError(
                    f"Module '{module.name}' needs input '{key}', which module "
                    f"'{elsewhere[key]}' produces — list '{elsewhere[key]}' too, "
                    f"or remove '{module.name}'"
                )
    return GraphBuilder(modules, provided=provided).build()
