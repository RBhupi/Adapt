# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Registry-driven module config engine.

Asks each module that is about to run for its frozen config and returns them
keyed by the context key each module declares (``<name>_config``). A module's
options are validated by its own model (``module_options``) and the module
builds its config from them (``configure``).

Adding a module never edits this file: the engine never knows which modules
exist; it finds them in the registry.
"""

from collections.abc import Iterable, Mapping
from types import UnionType
from typing import Any, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, ConfigDict

from adapt.configuration.schemas.errors import ConfigError, OptionsRefused, validated
from adapt.configuration.schemas.internal import InternalConfig
from adapt.contracts import SharedConfig, config_key
from adapt.execution.module_registry import registry

__all__ = ["module_options", "recorded_options", "resolve_module_configs"]


class _NoOptions(BaseModel):
    """The options of a module that declares none: every key is unknown."""

    model_config = ConfigDict(extra="forbid")


def module_options(name: str, given: Mapping[str, Any], *, source: str) -> BaseModel | None:
    """Module ``name``'s validated options: its defaults with ``given`` laid over.

    The defaults are the module's packaged preset when it ships one, else those
    of its options model. ``given`` (a pipeline file's ``params.<name>``, or a
    run's recorded options) is laid over them by the model's schema: a part
    that is itself a model of options is changed key by key, so one key deep
    inside may be given alone; any other option — a list, a mapping of free
    keys, a ``null`` — replaces the default whole. None for a module without
    options.

    Raises
    ------
    OptionsRefused
        An option is unknown, missing, of the wrong type or out of its bounds;
        this ``ConfigError`` names ``source`` and each option as
        ``params.<name>.<path>``, then the command that prints the module's options.
    """
    cls = registry.get(name)
    model = _NoOptions if cls.options_class is None else cls.options_class
    preset = cls.options_preset()
    defaults = {} if preset is None else yaml.safe_load(preset)[name]
    try:
        options = validated(
            model, _laid_over(model, defaults, given), source=source, at=("params", name)
        )
    except OptionsRefused as exc:
        hint = f"The options of {name}: adapt config --show {name}"
        raise OptionsRefused(source, exc.lines, hints=[hint]) from exc
    return None if cls.options_class is None else options


def _laid_over(
    model: type[BaseModel], base: Mapping[str, Any], given: Mapping[str, Any]
) -> dict[str, Any]:
    """``base`` with ``given`` laid over it, part by part where ``model`` nests options."""
    merged = dict(base)
    for key, value in given.items():
        part = _options_model(model, key)
        ours = base.get(key)
        if part is not None and isinstance(value, Mapping) and isinstance(ours, Mapping):
            merged[key] = _laid_over(part, ours, value)
        else:
            merged[key] = value
    return merged


def _options_model(model: type[BaseModel], key: str) -> type[BaseModel] | None:
    """The model of options that ``model``'s field ``key`` holds (``X`` or ``X | None``), if any."""
    field = model.model_fields.get(key)
    if field is None:
        return None  # unknown: the model refuses it
    annotation = field.annotation
    held = get_args(annotation) if get_origin(annotation) in (Union, UnionType) else (annotation,)
    models = [arg for arg in held if isinstance(arg, type) and issubclass(arg, BaseModel)]
    return models[0] if len(models) == 1 else None


def recorded_options(internal_config: InternalConfig, name: str) -> BaseModel | None:
    """Module ``name``'s options as run config ``internal_config`` records them
    (its ``module_params`` entry), validated; None for a module without options.

    Raises
    ------
    ConfigError
        The config records no options for the module (a run records the options
        of every module it lists), or one of them is not valid; the message
        names the run.
    """
    source = f"module_params of run {internal_config.run_id}"
    if name not in internal_config.module_params:
        if registry.get(name).options_class is None:
            return None
        raise ConfigError(
            f"Invalid configuration ({source}): no options are recorded for module '{name}'. "
            "A run records the options of every module it lists"
        )
    return module_options(name, internal_config.module_params[name], source=source)


def resolve_module_configs(
    internal_config: InternalConfig, modules: Iterable[str]
) -> dict[str, Any]:
    """Build one frozen config per module of ``modules``, the modules about to run.

    Each is configured from the run's ``module_params`` entry for it
    (``recorded_options``, then its ``configure`` with the run's shared values),
    and may refuse what it needs and was not given; a registered module that
    does not run is never configured. Modules without a config contribute no key.
    """
    shared = SharedConfig(
        tracking_field=internal_config.global_.tracking_field,
        z_level=internal_config.global_.z_level,
        max_scan_gap_minutes=internal_config.global_.max_scan_gap_minutes,
        source=internal_config.source,
    )
    configs: dict[str, Any] = {}
    for name in modules:
        options = recorded_options(internal_config, name)
        module_cfg = registry.get(name).configure(options, shared)
        if module_cfg is not None:
            configs[config_key(name)] = module_cfg
    return configs
