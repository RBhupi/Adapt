# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Commented-YAML serializer for generated config files.

Emits a plain config dict as YAML, annotating scalar leaves with their field
descriptions as inline ``# comment`` text. Parsing the result reproduces the
original data (comments and tuple→list normalisation aside). Pure formatting.
"""

import re
from typing import Any

import yaml

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
