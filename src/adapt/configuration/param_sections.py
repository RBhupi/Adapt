# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""A pipeline file's ``params:`` sections, checked against their ParamConfig models.

``check_sections`` validates each section's options against its ParamConfig
model — bounds, types and unknown keys — and refuses an obsolete option: one
without a description (``obsolete``), marked ``# OBSOLETE`` in its schema
because nothing reads it, so setting it would have no effect.
"""

from collections.abc import Iterator, Mapping
from typing import Any

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from adapt.configuration.schemas.param import ParamConfig

__all__ = ["check_sections", "obsolete"]


def obsolete(field: FieldInfo) -> bool:
    """Whether an option is obsolete: it has no description, so nothing reads it."""
    return not field.description


def check_sections(sections: Mapping[str, Any]) -> None:
    """Validate ``sections`` (ParamConfig section name -> options) against ParamConfig.

    Raises
    ------
    ValueError
        An option is obsolete (named, with no effect), or — as pydantic's
        ``ValidationError`` — unknown, of the wrong type or out of its bounds.
    """
    found = [".".join(("params", *loc)) for loc, _ in _obsolete(ParamConfig, sections, ())]
    if found:
        raise ValueError(f"obsolete option(s) with no effect — remove: {', '.join(found)}")
    ParamConfig.model_validate(sections)


def _obsolete(
    model: type[BaseModel], options: Mapping[str, Any], at: tuple[str, ...]
) -> Iterator[tuple[tuple[str, ...], Any]]:
    """The obsolete options given in ``options`` of ``model``, nested ones included."""
    fields = {field.alias or name: field for name, field in model.model_fields.items()}
    for key, value in options.items():
        field = fields.get(key)
        if field is None:
            continue  # unknown: the model refuses it
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if isinstance(value, Mapping):
                yield from _obsolete(annotation, value, (*at, key))
        elif obsolete(field):
            yield (*at, key), value
