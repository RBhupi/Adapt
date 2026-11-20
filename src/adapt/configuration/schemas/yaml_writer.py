# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Commented-YAML serializer for generated config files.

Emits a plain config dict as YAML, annotating scalar leaves with their field
descriptions as inline ``# comment`` text. Parsing the result reproduces the
original data (comments and tuple→list normalisation aside). ``insert`` adds
such entries to a mapping of an existing YAML text, leaving every other line as
it is. Pure formatting.
"""

import re
import textwrap
from collections.abc import Sequence
from typing import Any

import yaml

from adapt.configuration.schemas.errors import ConfigError

# Descriptions are a tree mirroring the data: a key maps to either a leaf
# description string or a nested dict of descriptions. A nested dict's
# ``_header`` is a comment written above its key.
Descriptions = dict[str, Any]

_PLAIN = re.compile(r"^[A-Za-z0-9_./+-]+$")


def dump(data: dict, descriptions: Descriptions | None = None, header: str = "") -> str:
    """Serialize ``data`` to commented YAML.

    Parameters
    ----------
    data : dict
        Config to serialize (scalars, lists/tuples, nested dicts).
    descriptions : dict, optional
        Tree of per-field descriptions mirroring ``data``; scalar leaves with a
        description get an inline ``# comment``.
    header : str, optional
        Pre-formatted comment block placed at the top (caller includes ``#``).
    """
    lines: list[str] = []
    if header:
        lines.append(header.rstrip("\n"))
    _emit(data, descriptions or {}, 0, lines)
    return "\n".join(lines) + "\n"


def _emit(data: dict, desc: Descriptions, indent: int, lines: list[str]) -> None:
    pad = "  " * indent
    for key, value in data.items():
        d = desc.get(key) if isinstance(desc, dict) else None
        comment = f"  # {d}" if isinstance(d, str) and d else ""
        # Keys pass through scalar too: an unquoted key containing ': ' or
        # '#' (possible in user field_map source names) emits unparseable
        # YAML — the same defect class as the double-quoted-path incident.
        k = scalar(key)
        if isinstance(value, dict):
            if not value:
                # A bare 'key:' reloads as None, not {} — round-trip breaks.
                lines.append(f"{pad}{k}: {{}}{comment}")
                continue
            header = d.get("_header") if isinstance(d, dict) else None
            if isinstance(header, str) and header:
                lines.extend(f"{pad}# {hl}" for hl in header.splitlines())
            lines.append(f"{pad}{k}:{comment}")
            _emit(value, d if isinstance(d, dict) else {}, indent + 1, lines)
        elif isinstance(value, list | tuple):
            _emit_sequence(k, value, comment, indent, lines)
        else:
            lines.append(f"{pad}{k}: {scalar(value)}{comment}")


def _emit_sequence(key: str, value, comment: str, indent: int, lines: list[str]) -> None:
    """A list at ``key``; ``comment`` ends its first line."""
    pad = "  " * indent
    items = list(value)
    if items and all(_flat_mapping(i) for i in items):
        # Flat mappings → one inline row each.
        lines.append(f"{pad}{key}:{comment}")
        for item in items:
            inline = ", ".join(f"{scalar(k)}: {scalar(v)}" for k, v in item.items())
            lines.append(f"{pad}  - {{{inline}}}")
        return
    if any(isinstance(i, dict) for i in items):
        # Emitting only the dict keys (what the naive path would do) is
        # silent corruption — refuse loudly instead.
        raise ValueError(
            f"yaml_writer: cannot serialize a list of dicts at key {key!r} — "
            "flatten the schema or extend the writer first"
        )
    if all(not isinstance(i, list | tuple | dict) for i in items):
        # Flat sequence → inline flow style.
        inline = ", ".join(scalar(i) for i in items)
        lines.append(f"{pad}{key}: [{inline}]{comment}")
        return
    # Nested sequence → block style, one inline row per item.
    lines.append(f"{pad}{key}:{comment}")
    item_pad = "  " * (indent + 1)
    for item in items:
        inline = ", ".join(scalar(i) for i in item)
        lines.append(f"{item_pad}- [{inline}]")


def _flat_mapping(item: Any) -> bool:
    return isinstance(item, dict) and not any(
        isinstance(v, dict | list | tuple) for v in item.values()
    )


def insert(text: str, at: Sequence[str], entries: str, gap: int = 0) -> str:
    """``text``, a YAML document, with ``entries`` added at the end of its mapping at ``at``.

    ``at`` is a key path. ``entries`` is YAML from column 0 (what ``dump``
    writes); it is indented to the mapping's keys, after ``gap`` empty lines.
    A key with no value yet (``params:``) takes them indented two spaces past
    it. Every line of ``text`` is kept as it is, comments included.

    Raises
    ------
    ConfigError
        ``at`` holds something other than a block mapping or no value — a flow
        mapping ``{...}``, a ``null``, a list or a scalar — so no line can be added
        to it.
    """
    key, value = _find(yaml.compose(text), at)
    if _no_value(value):
        indent, after = key.start_mark.column + 2, key.end_mark.line
    elif isinstance(value, yaml.MappingNode) and not value.flow_style:
        indent, after = value.value[0][0].start_mark.column, _last_line(value)
    else:
        raise ConfigError(
            f"{'.'.join(at)} is not written one entry per line (line {key.start_mark.line + 1}), "
            "so options cannot be added to it — write it as a block mapping, one option per line"
        )
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    added = ["\n"] * gap + [textwrap.indent(entries, " " * indent)]
    return "".join([*lines[: after + 1], *added, *lines[after + 1 :]])


def _find(node: yaml.Node | None, at: Sequence[str]) -> tuple[yaml.Node, yaml.Node]:
    """The key and value nodes at key path ``at`` of the document ``node``."""
    key: yaml.Node | None = None
    for depth, name in enumerate(at, start=1):
        if not isinstance(node, yaml.MappingNode):
            raise ConfigError(f"{'.'.join(at[: depth - 1]) or 'The document'} is not a mapping")
        found = [(k, v) for k, v in node.value if k.value == name]
        if not found:
            raise ConfigError(f"{'.'.join(at[:depth])} is not in the file")
        key, node = found[0]
    assert key is not None and node is not None, "at names at least one key"
    return key, node


def _no_value(node: yaml.Node) -> bool:
    """A key written with nothing after it: ``params:``."""
    return isinstance(node, yaml.ScalarNode) and node.tag.endswith(":null") and node.value == ""


def _last_line(node: yaml.Node) -> int:
    """The index of the last line that holds part of ``node``."""
    if isinstance(node, yaml.MappingNode) and not node.flow_style:
        key, value = node.value[-1]
        return key.end_mark.line if _no_value(value) else _last_line(value)
    if isinstance(node, yaml.SequenceNode) and not node.flow_style:
        return _last_line(node.value[-1])
    end = node.end_mark
    return end.line if end.column else end.line - 1


def scalar(value: Any) -> str:
    """Render a scalar as a YAML token."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        # Plain only when YAML reads it back as this same string: '2025',
        # 'true' or 'null' unquoted would reload as an int, a bool or None.
        if _PLAIN.match(value) and yaml.safe_load(value) == value:
            return value
        # Single-quoted, not double-quoted: YAML processes escape sequences inside
        # double quotes, so a backslash would corrupt the value (and "\U" in a
        # Windows path is a hard parse error). In single quotes the only escape is
        # '' for a literal quote, so any path or free text round-trips verbatim.
        return "'" + value.replace("'", "''") + "'"
    return str(value)
