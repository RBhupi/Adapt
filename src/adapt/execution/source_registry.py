# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Source registry — the one in-process table of source classes.

A source is where a run's scans come from (``run.source``). The catalog lists
each source's class under ``pipeline.sources`` of ``configuration/defaults.yaml``;
``catalog_source_names`` registers the class each line names, under its
``source_name``. Adding a source = its package under ``sources/`` and one
catalog line; nothing here names one.

A source class carries, besides the ``ScanSource`` methods, three class
attributes read once when it is registered: ``source_name`` (the name under
``run.source``; not ``name``, which is the thread's), ``summary`` (one line)
and ``options_class`` (its options model, or ``None`` for a source without
options).
"""

from pydantic import BaseModel

from adapt.contracts.source import ScanSource
from adapt.execution.catalog import CATALOG, catalog_lines, named_class

__all__ = ["SourceRegistry", "catalog_source_names", "source_registry"]


class SourceRegistry:
    """Name → source class, with each source's summary and options model.

    Raises
    ------
    RuntimeError
        If one name is registered twice.
    KeyError
        If ``get``, ``summary`` or ``options_class`` is asked for a name not
        registered.
    ValueError
        If the class lacks one of the three class attributes.
    """

    def __init__(self) -> None:
        self._sources: dict[str, type[ScanSource]] = {}
        self._summaries: dict[str, str] = {}
        self._options: dict[str, type[BaseModel] | None] = {}

    def register(self, source_class: type[ScanSource]) -> None:
        name = _text_attribute(source_class, "source_name")
        summary = _text_attribute(source_class, "summary")
        options = _options_attribute(source_class)
        if name in self._sources:
            raise RuntimeError(
                f"Source '{name}' is already registered by {self._sources[name]}. "
                f"Cannot register {source_class} with the same name."
            )
        self._sources[name] = source_class
        self._summaries[name] = summary
        self._options[name] = options

    def unregister(self, name: str) -> None:
        """Remove a source (primarily for testing)."""
        self._sources.pop(name, None)
        self._summaries.pop(name, None)
        self._options.pop(name, None)

    def get(self, name: str) -> type[ScanSource]:
        if name not in self._sources:
            raise KeyError(f"Source '{name}' is not registered.")
        return self._sources[name]

    def summary(self, name: str) -> str:
        self.get(name)
        return self._summaries[name]

    def options_class(self, name: str) -> type[BaseModel] | None:
        """The options model of ``name``; ``None`` for a source without options."""
        self.get(name)
        return self._options[name]

    def names(self) -> list[str]:
        return list(self._sources)

    def __contains__(self, name: str) -> bool:
        return name in self._sources


def _text_attribute(source_class: type, attribute: str) -> str:
    value = getattr(source_class, attribute, None)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{source_class} has no {attribute} (a class attribute, non-empty text)")
    return value


def _options_attribute(source_class: type) -> type[BaseModel] | None:
    if not hasattr(source_class, "options_class"):
        raise ValueError(f"{source_class} has no options_class (a class attribute)")
    value = source_class.options_class
    if value is not None and not (isinstance(value, type) and issubclass(value, BaseModel)):
        raise ValueError(
            f"{source_class} has options_class {value!r}: it must be None or a pydantic model"
        )
    return value


def _source_class(line: object) -> type[ScanSource]:
    """The source class a catalog line names, checked as ``register`` would."""
    named = named_class(line)
    if named is ScanSource or not issubclass(named, ScanSource):
        raise RuntimeError(
            f"Catalog line {line!r} in '{CATALOG}' names no source class "
            "(a class with the methods of contracts.source.ScanSource)"
        )
    try:
        _text_attribute(named, "source_name")
        _text_attribute(named, "summary")
        _options_attribute(named)
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
        ``source_name``, ``summary`` or valid ``options_class``, or two classes
        give one name.
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
