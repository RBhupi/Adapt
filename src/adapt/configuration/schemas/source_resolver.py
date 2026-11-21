# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The options of a run's source, validated by the source's own model.

A pipeline file sets them under ``params.<source name>``; a run records them,
complete, as ``InternalConfig.source_params``. Which sources exist, and the
model of each, are the source registry's: this file names no source.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from adapt.configuration.schemas.base import NoOptions
from adapt.configuration.schemas.errors import ConfigError, OptionsRefused, validated
from adapt.configuration.schemas.internal import InternalConfig
from adapt.execution.source_registry import source_registry

__all__ = ["recorded_source_options", "source_defaults", "source_options"]


def source_options(name: str, given: Mapping[str, Any], *, source: str) -> BaseModel | None:
    """Source ``name``'s validated options: its model's defaults with ``given``
    laid over; None for a source without options.

    Raises
    ------
    OptionsRefused
        An option is unknown, of the wrong type or out of its bounds; the
        message names ``source`` and each option as ``params.<name>.<option>``,
        then the command that prints the source's options.
    """
    model = source_registry.options_class(name)
    try:
        options = validated(model or NoOptions, dict(given), source=source, at=("params", name))
    except OptionsRefused as exc:
        hint = f"The options of {name}: adapt config --params {name}"
        raise OptionsRefused(source, exc.lines, hints=[hint]) from exc
    return None if model is None else options


def source_defaults(name: str) -> dict[str, Any]:
    """Source ``name``'s default options as a file would give them (``{}`` for a
    source without options)."""
    model = source_registry.options_class(name)
    return {} if model is None else model().model_dump()


def recorded_source_options(config: InternalConfig) -> BaseModel | None:
    """The options of run ``config``'s source as the run records them
    (``source_params``), validated; None for a source without options.

    Raises
    ------
    ConfigError
        The run records no options for a source that has some, or one of them
        is not valid; the message names the run.
    """
    source = f"source_params of run {config.run_id}"
    if config.source_params is None:
        if source_registry.options_class(config.source) is None:
            return None
        raise ConfigError(
            f"Invalid configuration ({source}): no options are recorded for source "
            f"'{config.source}'. A run records the options of its source"
        )
    return source_options(config.source, config.source_params, source=source)
