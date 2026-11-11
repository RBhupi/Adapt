# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt config --show NAME``: NAME's options with their defaults, as YAML to paste.

The output is lines to paste under the ``params:`` line of a pipeline file: a
first comment line saying so, then the entries indented two spaces. NAME is a
module or a ``ParamConfig`` section:

- a module whose options live in params sections prints those sections;
- a params section (``global``, ``segmenter``, ...) prints itself;
- a module that owns its options prints them under its own name, ``NAME: ...``
  — its packaged preset with its comments when it ships one.

Each option carries the first sentence of its description (and the allowed
values, for a choice) as a comment. Obsolete options (``param_sections.obsolete``)
are not offered, and a section with no other option says so; neither are the
downloader options that the pipeline file's ``run:`` section owns.
"""

import textwrap
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from adapt.configuration.param_sections import obsolete
from adapt.configuration.pipeline_file import RUN_OWNED_DOWNLOADER_KEYS, param_section_names
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.param import ParamConfig
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
    if name in param_section_names():
        return _sections((name,))
    if name in modules:
        cls = registry.get(name)
        header = f"# {name}: {cls.module_summary()}"
        if cls.param_sections:
            return _sections(
                cls.param_sections,
                f"{header}. Options shared by every module: adapt config --show global",
            )
        packaged = cls.options_text()
        if packaged is not None:
            note = (
                f"# These are {name}'s defaults: listed under modules:, it runs with them.\n"
                "# To change an option, give only the part that holds it; the rest keep these.\n"
            )
            return _paste("params", note + packaged)
        data, notes = (
            ({}, {})  # a module without options
            if cls.config_class is None
            else _options(cls.config_class, cls.default_params(), cls.injected_global_fields)
        )
        return _paste("params", dump({name: data}, {name: notes}, header))
    raise ConfigError(
        f"Unknown name '{name}' for adapt config --show. Valid names:\n"
        f"  modules: {', '.join(modules)}\n"
        f"  params sections: {', '.join(param_section_names())}"
    )


def _sections(names: tuple[str, ...], header: str = "") -> str:
    defaults = ParamConfig().model_dump(by_alias=True)
    models = {
        field.alias or attr: field.annotation for attr, field in ParamConfig.model_fields.items()
    }
    data: dict[str, Any] = {}
    notes: Descriptions = {}
    for name in names:
        skip = frozenset(RUN_OWNED_DOWNLOADER_KEYS) if name == "downloader" else frozenset()
        options, comments = _options(models[name], defaults[name], skip)
        if options:  # a section of obsolete options only has nothing to set
            data[name], notes[name] = options, comments
    if not data:
        held = ", ".join(f"params.{name}" for name in names)
        return f"# {held} has no options to set: those it had are obsolete and have no effect\n"
    return _paste("params", dump(data, notes, header))


def _paste(line: str, entries: str) -> str:
    """``entries`` (YAML from column 0) as lines to paste under the file's ``line:`` line."""
    return f"# Paste these lines under the {line}: line of your pipeline file\n" + textwrap.indent(
        entries, "  "
    )


def _options(model: Any, values: dict[str, Any], skip: frozenset[str]) -> tuple[dict, Descriptions]:
    """The described options of ``model`` with their ``values``, and their comments."""
    data: dict[str, Any] = {}
    notes: Descriptions = {}
    for attr, field in model.model_fields.items():
        key = field.alias or attr
        if key in skip or obsolete(field):
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
