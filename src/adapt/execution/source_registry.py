# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Source registry — the one in-process table of source classes.

A source is where a run's scans come from (``run.source``). The catalog lists
each source's class under ``pipeline.sources`` of ``configuration/defaults.yaml``;
``catalog_source_names`` registers the class each line names, under its
``source_name``. Adding a source = its package under ``sources/`` and one
catalog line; nothing here names one.

A source class carries, besides the ``ScanSource`` methods, two class
attributes read once when it is registered: ``source_name`` (the name under
``run.source``; not ``name``, which is the thread's) and ``summary`` (one line).
"""

from adapt.contracts.source import ScanSource
from adapt.execution.catalog import CATALOG, catalog_lines, named_class

__all__ = ["SourceRegistry", "catalog_source_names", "source_registry"]


class SourceRegistry:
    """Name → source class, with each source's summary.

    Raises
    ------
    RuntimeError
        If one name is registered twice.
    KeyError
        If ``get`` or ``summary`` is asked for a name not registered.
    """

    def __init__(self) -> None:
        self._sources: dict[str, type[ScanSource]] = {}
        self._summaries: dict[str, str] = {}

    def register(self, source_class: type[ScanSource]) -> None:
        name = _text_attribute(source_class, "source_name")
        summary = _text_attribute(source_class, "summary")
        if name in self._sources:
            raise RuntimeError(
                f"Source '{name}' is already registered by {self._sources[name]}. "
                f"Cannot register {source_class} with the same name."
            )
        self._sources[name] = source_class
        self._summaries[name] = summary

    def unregister(self, name: str) -> None:
        """Remove a source (primarily for testing)."""
        self._sources.pop(name, None)
        self._summaries.pop(name, None)

    def get(self, name: str) -> type[ScanSource]:
        if name not in self._sources:
            raise KeyError(f"Source '{name}' is not registered.")
        return self._sources[name]

    def summary(self, name: str) -> str:
        self.get(name)
        return self._summaries[name]

    def names(self) -> list[str]:
        return list(self._sources)

    def __contains__(self, name: str) -> bool:
        return name in self._sources


def _text_attribute(source_class: type, attribute: str) -> str:
    value = getattr(source_class, attribute, None)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{source_class} has no {attribute} (a class attribute, non-empty text)")
    return value


def _source_class(line: object) -> type[ScanSource]:
    """The source class a catalog line names, checked as ``register`` would."""
    named = named_class(line)
    if named is ScanSource or not issubclass(named, ScanSource):
        raise RuntimeError(
            f"Catalog line {line!r} in '{CATALOG}' names no source class "
            "(a class with the methods of contracts.source.ScanSource)"
        )
    for attribute in ("source_name", "summary"):
        try:
            _text_attribute(named, attribute)
        except ValueError as e:
            raise RuntimeError(f"Catalog line {line!r} in '{CATALOG}': {e}") from e
    return named


def catalog_source_names() -> list[str]:
    """Names of every catalog source, in the catalog's order.

    Registers the class each line names. Loading again leaves a class that is
    already registered alone, since several components load the catalog.

    Raises
    ------
    RuntimeError
        If the catalog is unreadable or its source list empty, a line is not
        ``package.file:Class``, names no source class or one without a
        ``source_name`` or ``summary``, or two classes give one name.
    ImportError
        If a line's file cannot be imported.
    """
    names: list[str] = []
    for line in catalog_lines("sources"):
        source_class = _source_class(line)
        name = _text_attribute(source_class, "source_name")
        if name not in source_registry or source_registry.get(name) is not source_class:
            source_registry.register(source_class)
        names.append(name)
    return names


source_registry = SourceRegistry()
