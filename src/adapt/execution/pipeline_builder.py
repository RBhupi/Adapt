# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Module discovery (the module catalog) and selection for pipeline assembly.

The catalog is one file, ``configuration/defaults.yaml``, with one line per
module naming its class as ``package.file:Class``. Loading the catalog
registers each class.

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
    ProductView,
    config_key,
)
from adapt.execution.graph.builder import GraphBuilder
from adapt.execution.graph.node import Node
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule

logger = logging.getLogger(__name__)

_DEFAULTS_YAML = Path(__file__).parent.parent / "configuration" / "defaults.yaml"


def _catalog_lines() -> list[object]:
    """The lines of the catalog, ``pipeline.modules`` of ``configuration/defaults.yaml``."""
    try:
        with open(_DEFAULTS_YAML, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except OSError as e:
        raise RuntimeError(f"Cannot read pipeline module list '{_DEFAULTS_YAML}': {e}") from e

    lines = (cfg or {}).get("pipeline", {}).get("modules", [])
    if not lines:
        raise RuntimeError(
            f"No pipeline modules declared under 'pipeline.modules' in '{_DEFAULTS_YAML}'"
        )
    return list(lines)


def _module_class(line: object) -> type[BaseModule]:
    """The module class a catalog line names as ``package.file:Class``."""
    path, _, class_name = line.partition(":") if isinstance(line, str) else ("", "", "")
    if not path or not class_name:
        raise RuntimeError(
            f"Catalog line {line!r} in '{_DEFAULTS_YAML}' is not of the form package.file:Class"
        )
    try:
        named = getattr(importlib.import_module(path), class_name, None)
    except Exception as e:
        raise ImportError(f"Failed to import pipeline module '{line}': {e}") from e
    if not isinstance(named, type) or not issubclass(named, BaseModule) or named is BaseModule:
        raise RuntimeError(
            f"Catalog line {line!r} in '{_DEFAULTS_YAML}' names no module class "
            "(a BaseModule subclass)"
        )
    if not named.name:
        raise RuntimeError(
            f"Catalog line {line!r} in '{_DEFAULTS_YAML}': class {named.__name__} has no name "
            "(the name a pipeline file lists it by)"
        )
    return named


def catalog_module_names() -> list[str]:
    """Names of every catalog module, in the catalog's order.

    Registers the class each line names. Loading again leaves a class that is
    already registered alone, since several components load the catalog during
    startup.

    Raises
    ------
    RuntimeError
        If the catalog is unreadable or empty, a line is not
        ``package.file:Class``, names no module class or one without a name,
        or two classes give one module name.
    ImportError
        If a line's file cannot be imported.
    """
    names: list[str] = []
    for line in _catalog_lines():
        module_class = _module_class(line)
        name = module_class.name
        if name not in registry or registry.get(name) is not module_class:
            registry.register(module_class)
        names.append(name)
    return names


def _ensure_modules_registered() -> None:
    """Register the modules listed in configuration/defaults.yaml.

    To add a module: add its class to defaults.yaml as ``package.file:Class``.
    Raises as ``catalog_module_names`` does.
    """
    catalog_module_names()


def resolve_enabled_modules(all_modules: list, modules: list[str]) -> list:
    """Filter ``all_modules`` to the ``modules`` list, in ``all_modules``' order.

    Every listed name must be a real module.

    Parameters
    ----------
    all_modules : list
        All registered module instances (each with ``name``/``inputs``/``outputs``).
    modules : list[str]
        The run's module list.
    """
    by_name = {m.name: m for m in all_modules}
    for n in modules:
        if n not in by_name:
            raise ValueError(f"Unknown module '{n}' in modules. Available: {', '.join(by_name)}")
    return [m for m in all_modules if m.name in modules]


def stored_output_keys() -> frozenset[str]:
    """Output keys of the registered modules that are stored as an artifact or a
    table, and so can be read back from the store."""
    return frozenset(
        spec.key
        for name in registry.list_modules()
        for spec in registry.get(name).persistence
        if isinstance(spec, NetcdfArtifact | ProductTableWrite)
    )


def product_views() -> tuple[ProductView, ...]:
    """The views the registered modules declare. A collection's writer is given
    all of them: a table joins a view whether or not the run lists the module
    that declares it."""
    return tuple(
        spec
        for name in registry.list_modules()
        for spec in registry.get(name).persistence
        if isinstance(spec, ProductView)
    )


def carried_keys(modules: list) -> frozenset[str]:
    """The keys the modules carry from one scan to the next: each both read and
    written by one module, whose last value the runtime hands back to it."""
    return frozenset(key for module in modules for key in module.inputs if key in module.outputs)


def _prior_inputs(module: Any, configs: Mapping[str, Any]) -> tuple[str, ...]:
    """The outputs of earlier scans ``module`` reads: what its options make it
    read (``prior_inputs_for`` its built config), or what it declares without one."""
    key = config_key(module.name)
    if key not in configs:
        return module.prior_inputs
    return type(module).prior_inputs_for(configs[key])


def prior_keys(modules: list, configs: Mapping[str, Any]) -> frozenset[str]:
    """The outputs the runtime keeps of each scan for the scans that follow: what
    the modules read back from earlier scans (``configs`` as for ``scan_graph``)."""
    return frozenset(key for module in modules for key in _prior_inputs(module, configs))


def prior_depth(modules: list, configs: Mapping[str, Any]) -> int:
    """How many earlier scans the runtime keeps for ``prior_scans``: the most any
    listed module needs (``configs`` as for ``scan_graph``).

    A module needs the ``required_history - 1`` scans it waits for. One that
    waits for none but reads outputs of earlier scans (``prior_inputs_for``)
    needs the last one. None when no module does either.
    """
    return max(
        (
            max(module.required_history - 1, 1 if _prior_inputs(module, configs) else 0)
            for module in modules
        ),
        default=0,
    )


def scan_graph(
    modules: list, configs: Mapping[str, Any], *, stored_run: bool = False
) -> list[Node]:
    """The one per-scan graph of ``modules``, as the processor runs it.

    The runtime provides ``SCAN_SEEDS``, one config per module, the
    ``STORE_VIEWS`` and each module's own carried state (``carried_keys``); every
    other input must be another listed module's output.
    Over a stored run (``stored_run``) an input may also be any output a
    registered module stores: the runtime reads it back, scan by scan. A module
    with a config in ``configs`` (each module's built config, by ``config_key``)
    reads what its options make it read (``inputs_for``, ``prior_inputs_for``);
    one without, what it declares.

    What a module reads of earlier scans (``prior_scans``) must be an output of a
    listed module, itself included: the runtime keeps it from scan to scan and
    never reads it back from the store, stored run or not.

    Raises
    ------
    ValueError
        Two modules declare one output key, or an input (of this scan or of
        earlier scans) has no source — naming the module that produces it, when
        one is registered but not listed.
    """
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
    for module in modules:
        for key in _prior_inputs(module, configs):
            if key in produced:
                continue
            reads = f"Module '{module.name}' reads output '{key}' of earlier scans"
            if key not in elsewhere:
                raise ValueError(f"{reads}, which no registered module produces")
            raise ValueError(
                f"{reads}, which module '{elsewhere[key]}' produces — list "
                f"'{elsewhere[key]}' under modules:, or change {module.name}'s options so "
                f"it does not read it (adapt config --params {module.name})"
            )
    return GraphBuilder(modules, provided=provided).build()
