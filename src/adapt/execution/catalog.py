# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The catalog: ``configuration/defaults.yaml``, the one file that names every
class a pipeline can plug in.

It holds one list per kind (``pipeline.modules``, ``pipeline.sources``), one
line per class, written ``package.file:Class``. The module and source loaders
read their list here and import the class each line names; each checks that the
class is of its kind. Everything fails loudly, naming the catalog and the line.
"""

import importlib
from pathlib import Path

import yaml

__all__ = ["CATALOG", "catalog_lines", "named_class"]

CATALOG = Path(__file__).parent.parent / "configuration" / "defaults.yaml"


def catalog_lines(kind: str) -> list[object]:
    """The lines of ``pipeline.<kind>`` (``modules`` or ``sources``) of the catalog.

    Raises
    ------
    RuntimeError
        If the catalog cannot be read or the list is missing or empty.
    """
    try:
        with open(CATALOG, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except OSError as e:
        raise RuntimeError(f"Cannot read the catalog '{CATALOG}': {e}") from e

    lines = (cfg or {}).get("pipeline", {}).get(kind, [])
    if not lines:
        raise RuntimeError(f"No pipeline {kind} declared under 'pipeline.{kind}' in '{CATALOG}'")
    return list(lines)


def named_class(line: object) -> type:
    """The class a catalog line names as ``package.file:Class``.

    Raises
    ------
    RuntimeError
        If the line is not ``package.file:Class`` or names no class.
    ImportError
        If the line's file cannot be imported.
    """
    path, _, class_name = line.partition(":") if isinstance(line, str) else ("", "", "")
    if not path or not class_name:
        raise RuntimeError(
            f"Catalog line {line!r} in '{CATALOG}' is not of the form package.file:Class"
        )
    try:
        named = getattr(importlib.import_module(path), class_name, None)
    except Exception as e:
        raise ImportError(f"Failed to import catalog line '{line}': {e}") from e
    if not isinstance(named, type):
        raise RuntimeError(f"Catalog line {line!r} in '{CATALOG}' names no class")
    return named
