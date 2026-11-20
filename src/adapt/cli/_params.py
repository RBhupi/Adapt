# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt config --params``: options with their defaults, printed or added to a pipeline file.

``params(names)`` prints lines to paste under the ``params:`` line of a
pipeline file: a first comment line saying so, then one entry per name,
indented two spaces, two empty lines between entries. A name is a module or a
section of the run config:

- a section (``global``, ``downloader``, ``logging``) prints itself;
- a module prints its options under its own name, after a heading line that
  names it: its packaged preset with its comments when it ships one, else
  every option of its model.

Each option carries the first sentence of its description (and the allowed
values, for a choice) as a comment. The downloader settings that the pipeline
file's ``run:`` section owns are not offered.

``add_params(text, modules)`` adds the same to a pipeline file's text: a module
its ``params`` lack gets its entry at the end of ``params``; a module it has
gets the options it lacks (``missing_options``), at the end of the mapping that
lacks them. Every other line is kept as it is, so adding twice adds nothing.
"""

import re
import textwrap
from collections.abc import Sequence
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

import yaml
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from adapt.configuration.pipeline_file import (
    RUN_OWNED_DOWNLOADER_KEYS,
    load_pipeline_file,
    param_section_models,
)
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.module_resolver import missing_options, module_defaults
from adapt.configuration.schemas.yaml_writer import Descriptions, dump, insert
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import catalog_module_names

__all__ = ["add_params", "add_to_file", "params"]

_SENTENCE_END = re.compile(r"(?<!\d)\. ")


def params(names: Sequence[str]) -> str:
    """What ``adapt config --params NAME...`` prints.

    Raises
    ------
    ConfigError
        A name is neither a module nor a section; the message lists every valid
        name by kind.
    """
    modules = catalog_module_names()
    sections = param_section_models()
    unknown = [name for name in names if name not in modules and name not in sections]
    if unknown:
        raise ConfigError(
            f"Unknown name(s) {', '.join(unknown)} for adapt config --params. Valid names:\n"
            f"  modules: {', '.join(modules)}\n"
            f"  params sections: {', '.join(sections)}"
        )
    entries = [
        _section(name, sections[name]) if name in sections else _module(name)
        for name in dict.fromkeys(names)
    ]
    return "# Paste these lines under the params: line of your pipeline file\n" + textwrap.indent(
        "\n\n".join(entries), "  "
    )


def add_to_file(path: Path, names: Sequence[str]) -> list[str]:
    """Add to the pipeline file at ``path`` the options its ``params`` lack, of the
    modules ``names`` (none: every module the file lists); the modules that
    gained some. The file is written only when one did.

    Raises
    ------
    ConfigError
        ``path`` is no file, is not a valid pipeline file, or a name is not a
        module (a params section included); nothing is written.
    """
    if not path.is_file():
        raise ConfigError(f"No pipeline file {path}")
    pipeline = load_pipeline_file(path)
    modules = catalog_module_names()
    unknown = [name for name in names if name not in modules]
    if unknown:
        raise ConfigError(
            f"Unknown module(s) {', '.join(unknown)} for adapt config --params --file. "
            f"Modules: {', '.join(modules)}. A params section "
            f"({', '.join(param_section_models())}) is not added to a file: print it with  "
            "adapt config --params NAME  and paste it"
        )
    text = path.read_text(encoding="utf-8")
    added, gained = add_params(text, list(dict.fromkeys(names)) or list(pipeline.modules))
    if gained:
        path.write_text(added, encoding="utf-8")
    return gained


def add_params(text: str, modules: Sequence[str]) -> tuple[str, list[str]]:
    """``text``, a pipeline file's, with the options its ``params`` lack of each
    of ``modules``; and the modules that gained some."""
    gained: list[str] = []
    for name in modules:
        given = yaml.safe_load(text)["params"] or {}
        if name not in given:
            text = insert(text, ("params",), _module(name), gap=2 if given else 0)
            gained.append(name)
            continue
        notes = _notes(registry.get(name).options_class)
        for at, entries in missing_options(name, given[name] or {}):
            text = insert(text, ("params", name, *at), dump(entries, _part(notes, at)))
            gained += [] if name in gained else [name]
    return text, gained


def _module(name: str) -> str:
    """Module ``name``'s entry: a heading line, then its options under its name."""
    cls = registry.get(name)
    heading = (
        f"# {name}: {cls.module_summary()}. "
        "Options shared by every module: adapt config --params global\n"
    )
    packaged = cls.options_preset()
    if packaged is not None:
        note = (
            f"# {name} has no defaults: a file that lists it must give every option below.\n"
            if cls.every_option_written
            else f"# These are {name}'s defaults: listed under modules:, it runs with them.\n"
            "# To change an option, give only the part that holds it; the rest keep these.\n"
        )
        return heading + note + packaged.rstrip("\n") + "\n"
    return heading + dump({name: module_defaults(name)}, {name: _notes(cls.options_class)})


def _section(name: str, model: type[BaseModel]) -> str:
    """Run-config section ``name``'s options with their defaults."""
    skip = frozenset(RUN_OWNED_DOWNLOADER_KEYS) if name == "downloader" else frozenset()
    defaults = {
        option: field.default for option, field in model.model_fields.items() if option not in skip
    }
    return dump({name: defaults}, {name: _notes(model, skip)})


def _notes(model: Any, skip: frozenset[str] = frozenset()) -> Descriptions:
    """The comments of ``model``'s options, nested as its options nest."""
    if model is None:
        return {}
    notes: Descriptions = {}
    for attr, field in model.model_fields.items():
        key = field.alias or attr
        if key in skip:
            continue
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            notes[key] = {"_header": _meaning(field), **_notes(annotation)}
        else:
            notes[key] = _meaning(field)
    return notes


def _part(notes: Descriptions, at: Sequence[str]) -> Descriptions:
    """The comments of the part of the options at key path ``at``."""
    for key in at:
        part = notes.get(key)
        notes = part if isinstance(part, dict) else {}
    return notes


def _meaning(field: FieldInfo) -> str:
    """The description's first sentence, then the allowed values of a choice."""
    # A period after a digit numbers the text ("1. Which cells"); it ends no sentence.
    first = _SENTENCE_END.split(" ".join((field.description or "").split()))[0].rstrip(".")
    choices = [
        str(choice)
        for arg in _alternatives(field.annotation)
        if get_origin(arg) is Literal
        for choice in get_args(arg)
    ]
    return f"{first}: {' | '.join(choices)}" if choices else first


def _alternatives(annotation: Any) -> tuple[Any, ...]:
    if get_origin(annotation) in (Union, UnionType):
        return get_args(annotation)
    return (annotation,)
