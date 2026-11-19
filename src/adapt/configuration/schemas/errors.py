# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Human-readable configuration errors.

Pydantic's raw ``ValidationError`` names the offending path but buries it in a
stack trace and codes. ``validated()`` translates it into a ``ConfigError`` that
states, per problem, which config section/field is wrong, its value, and why —
so a stale or mistyped option reads clearly instead of dumping a traceback.
"""

from collections.abc import Iterable, Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

__all__ = ["ConfigError", "OptionsRefused", "together", "validated"]

M = TypeVar("M", bound=BaseModel)


class ConfigError(Exception):
    """A configuration value is invalid. Carries a ready-to-print message."""


class OptionsRefused(ConfigError):
    """Options of ``source`` are wrong.

    The message is a heading that names ``source``, then ``lines`` (one per
    wrong option: its full path and why), then ``hints`` (lines that say where
    to look the options up). The parts are kept, so the refusals of several
    places can be told as one (``together``).
    """

    def __init__(self, source: str, lines: Iterable[str], hints: Iterable[str] = ()) -> None:
        self.source = source
        self.lines = tuple(lines)
        self.hints = tuple(hints)
        super().__init__(_message({source: self.lines}, self.hints))


def together(refusals: Iterable[OptionsRefused]) -> ConfigError:
    """One error that tells every refusal of ``refusals``: each source once, with
    the wrong options found in it, then every hint."""
    by_source: dict[str, list[str]] = {}
    hints: list[str] = []
    for refusal in refusals:
        by_source.setdefault(refusal.source, []).extend(refusal.lines)
        hints.extend(refusal.hints)
    return ConfigError(_message(by_source, hints))


def _message(by_source: Mapping[str, Iterable[str]], hints: Iterable[str]) -> str:
    blocks = [
        line
        for source, lines in by_source.items()
        for line in (f"Invalid configuration ({source}):", *lines)
    ]
    return "\n".join([*blocks, *hints])


# Trailing location parts pydantic adds for the branches of a union (``int | float``).
_BRANCHES = frozenset({"int", "float", "str", "bool", "none", "list", "tuple", "dict"})


def _shown(value: Any) -> str:
    """The offending value, when it is short enough to help."""
    return f" (value: {value!r})" if isinstance(value, str | int | float | bool | None) else ""


def _reason(error: Mapping[str, Any], path: tuple[str, ...]) -> str:
    """One-line 'why' for a single pydantic error at ``path``, keyed off its type."""
    kind = error["type"]
    value = error.get("input")
    if kind == "extra_forbidden":
        return (
            f"unrecognized option{_shown(value)} — not a valid '{_holder(error, path)}' "
            "setting; remove it or check for a typo or a renamed field"
        )
    if kind == "missing":
        return "required option is missing"
    if kind in ("greater_than", "greater_than_equal", "less_than", "less_than_equal"):
        return f"value {value!r} is out of range — {error['msg']}"
    if kind in ("model_type", "dict_type", "model_attributes_type"):
        return "must be a mapping of options (key: value lines indented under it)"
    if kind in ("list_type", "tuple_type"):
        return "must be a list (one '- item' line per entry)"
    return f"{error['msg']}{_shown(value)}"


def _holder(error: Mapping[str, Any], path: tuple[str, ...]) -> str:
    """What holds the option at ``path``: its section, by name; an item of a list
    has no name, so that is told by its dotted path."""
    loc = error["loc"]
    if len(loc) >= 2 and isinstance(loc[-2], int):
        return ".".join(path[:-1])
    return path[-2] if len(path) >= 2 else "configuration"


def _path(error: Mapping[str, Any]) -> tuple[str, ...]:
    """The option's location, without the branch names of a union type."""
    parts = [str(part) for part in error["loc"]]
    while parts and parts[-1] in _BRANCHES:
        parts.pop()
    return tuple(parts)


def _refused(exc: ValidationError, source: str, at: tuple[str, ...]) -> OptionsRefused:
    """One line per option, its path prefixed with ``at``: an option that fails
    every branch of a union type is told once, with the first branch's reason."""
    lines = []
    seen: set[tuple[str, ...]] = set()
    for error in exc.errors():
        path = (*at, *_path(error))
        if path in seen:
            continue
        seen.add(path)
        lines.append(f"  • {'.'.join(path) or '(root)'} — {_reason(error, path)}")
    return OptionsRefused(source, lines)


def validated(model_cls: type[M], data: Any, *, source: str, at: tuple[str, ...] = ()) -> M:
    """Validate ``data`` against ``model_cls``, raising ConfigError on failure.

    ``source`` names where the config came from (e.g. "pipeline file
    nexrad.yaml") so the message points the user at the right place to fix;
    ``at`` is where ``data`` sits in it (e.g. ``("params", "<module>")``), so
    each option is named by its full path.

    Raises
    ------
    OptionsRefused
        ``data`` is not valid: a ``ConfigError`` with one line per wrong option.
    """
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise _refused(exc, source, at) from exc
