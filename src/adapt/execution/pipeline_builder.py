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


def store_read_tables(source: str) -> list[ProductTableWrite]:
    """The product tables the rows of ``source`` are made of, as the registered
    modules declare them; the table that names its rows comes first.

    ``source`` is a table, or a view: then its row table (``rows_from``), the
    tables its computed columns read, and every table with the row table's
    primary key — the tables the store joins into the view, whichever module
    writes them. A table declared by two modules counts once.

    Raises
    ------
    ValueError
        No registered module declares ``source`` as a table or as a view.
    """
    declared: dict[str, ProductTableWrite] = {}
    for name in registry.list_modules():
        for spec in registry.get(name).persistence:
            if isinstance(spec, ProductTableWrite):
                declared.setdefault(spec.table, spec)
    if source in declared:
        return [declared[source]]
    view = next((v for v in product_views() if v.name == source), None)
    if view is None or view.rows_from not in declared:
        raise ValueError(f"'{source}' is not a table or a view that a registered module declares")
    rows = declared[view.rows_from]
    key = set(rows.primary_key)
    joined = (
        table
        for table in declared.values()
        if table is not rows and (table.table in view.reads or set(table.primary_key) == key)
    )
    return [rows, *joined]


def store_read_edges(modules: list) -> list[tuple[str, str, str]]:
    """``(writer, reader, source)`` for every listed module that writes a table a
    listed module's store read is made of: the reader runs once the writer has
    stored the scan. Ordered by reader, then by its reads, then by writer."""
    return [
        (writer.name, reader.name, read.source)
        for reader in modules
        for read in reader.store_reads
        for writer in modules
        if writer is not reader
        and _writes_one_of(writer, {table.table for table in store_read_tables(read.source)})
    ]


def _writes_one_of(module: Any, tables: set[str]) -> bool:
    """Whether ``module`` (a module or its class) declares a write to one of ``tables``."""
    return any(
        isinstance(spec, ProductTableWrite) and spec.table in tables for spec in module.persistence
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

    The runtime provides ``SCAN_SEEDS``, one config per module, each module's
    own carried state (``carried_keys``) and the rows each module declares it
    reads from the store (``store_reads``); every other input must be another
    listed module's output.
    Over a stored run (``stored_run``) an input may also be any output a
    registered module stores: the runtime reads it back, scan by scan. A module
    with a config in ``configs`` (each module's built config, by ``config_key``)
    reads what its options make it read (``inputs_for``, ``prior_inputs_for``);
    one without, what it declares.

    What a module reads of earlier scans (``prior_scans``) must be an output of a
    listed module, itself included: the runtime keeps it from scan to scan and
    never reads it back from the store, stored run or not.

    A store read (``adapt.contracts.StoreRead``) is made just before its module
    runs, so the module runs after every listed module that writes a table the
    read's source is made of (``store_read_edges``): an order edge, without data.

    Raises
    ------
    ValueError
        Two modules declare one output key, or an input (of this scan or of
        earlier scans) has no source — naming the module that produces it, when
        one is registered but not listed. A store read whose source no
        registered module declares; one up to the scan of rows that are not per
        scan; one, in a run that makes new scans, of rows no listed module
        writes — naming the module that writes them.
    """
    for module in modules:
        key = config_key(module.name)
        if key in configs:
            module.inputs = type(module).inputs_for(configs[key])
    provided = SCAN_SEEDS | {config_key(m.name) for m in modules}
    provided |= {read.key for module in modules for read in module.store_reads}
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
    _check_store_reads(modules, stored_run)
    return GraphBuilder(modules, provided=provided, order_edges=store_read_edges(modules)).build()


def _check_store_reads(modules: list, stored_run: bool) -> None:
    """Refuse a store read that cannot be made (``ValueError``)."""
    for module in modules:
        for read in module.store_reads:
            reads = f"Module '{module.name}' reads '{read.source}' from the store"
            try:
                rows = store_read_tables(read.source)[0]
            except ValueError as exc:
                raise ValueError(f"{reads}: {exc}") from exc
            if read.up_to_scan and "scan_id" not in rows.primary_key:
                raise ValueError(
                    f"{reads} up to the scan, but its rows are not per scan (key: "
                    f"{', '.join(rows.primary_key)}) — declare the read with up_to_scan=False"
                )
            if stored_run or any(_writes_one_of(m, {rows.table}) for m in modules):
                continue
            writer = next(
                name
                for name in registry.list_modules()
                if _writes_one_of(registry.get(name), {rows.table})
            )
            raise ValueError(
                f"{reads}, whose rows module '{writer}' writes (table '{rows.table}') — "
                f"list '{writer}' too, or remove '{module.name}'"
            )
