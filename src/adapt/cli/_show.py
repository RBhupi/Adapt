# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt config --show NAME``: NAME's options with their defaults, as YAML to paste.

The output is lines to paste under the ``params:`` line of a pipeline file: a
first comment line saying so, then the entries indented two spaces. NAME is a
module or a section of the run config:

- a section (``global``, ``downloader``, ``logging``) prints itself;
- a module prints its options under its own name, ``NAME: ...`` — its packaged
  preset with its comments when it ships one, else under a header line that
  names the module and the command showing the options every module shares.

Each option carries the first sentence of its description (and the allowed
values, for a choice) as a comment. The downloader settings that the pipeline
file's ``run:`` section owns are not offered.
"""

import textwrap
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from adapt.configuration.pipeline_file import RUN_OWNED_DOWNLOADER_KEYS, param_section_models
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.yaml_writer import Descriptions, dump
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import catalog_module_names

__all__ = ["show"]


def show(name: str) -> str:
    """The YAML fragment that ``adapt config --show NAME`` prints.

    Raises
    ------
    ConfigError
        ``name`` is none of the above; the message lists every valid name by kind.
    """
    modules = catalog_module_names()
    sections = param_section_models()
    if name in sections:
        return _section(name, sections[name])
    if name in modules:
        cls = registry.get(name)
        packaged = cls.options_preset()
        if packaged is not None:
            note = (
                f"# These are {name}'s defaults: listed under modules:, it runs with them.\n"
                "# To change an option, give only the part that holds it; the rest keep these.\n"
            )
            return _paste("params", note + packaged)
        data, notes = (
            ({}, {})  # a module without options
            if cls.options_class is None
            else _options(cls.options_class, cls.options_class().model_dump(), frozenset())
        )
        header = (
            f"# {name}: {cls.module_summary()}. "
            "Options shared by every module: adapt config --show global"
        )
        return _paste("params", dump({name: data}, {name: notes}, header))
    raise ConfigError(
        f"Unknown name '{name}' for adapt config --show. Valid names:\n"
        f"  modules: {', '.join(modules)}\n"
        f"  params sections: {', '.join(sections)}"
    )


def _section(name: str, model: type[BaseModel]) -> str:
    """Run-config section ``name``'s options with their defaults."""
    skip = frozenset(RUN_OWNED_DOWNLOADER_KEYS) if name == "downloader" else frozenset()
    defaults = {
        option: field.default for option, field in model.model_fields.items() if option not in skip
    }
    data, notes = _options(model, defaults, skip)
    return _paste("params", dump({name: data}, {name: notes}))


def _paste(line: str, entries: str) -> str:
    """``entries`` (YAML from column 0) as lines to paste under the file's ``line:`` line."""
    return f"# Paste these lines under the {line}: line of your pipeline file\n" + textwrap.indent(
        entries, "  "
    )


def _options(model: Any, values: dict[str, Any], skip: frozenset[str]) -> tuple[dict, Descriptions]:
    """The options of ``model`` with their ``values``, and their comments."""
    data: dict[str, Any] = {}
    notes: Descriptions = {}
    for attr, field in model.model_fields.items():
        key = field.alias or attr
        if key in skip:
            continue
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            data[key], nested = _options(annotation, values[key], frozenset())
            notes[key] = {"_header": _meaning(field), **nested}
        else:
            data[key], notes[key] = values[key], _meaning(field)
    return data, notes


def _meaning(field: FieldInfo) -> str:
    """The description's first sentence, then the allowed values of a choice."""
    first = " ".join((field.description or "").split()).split(". ")[0].rstrip(".")
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
