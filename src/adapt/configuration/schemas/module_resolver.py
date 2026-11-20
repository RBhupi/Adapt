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

__all__ = [
    "missing_options",
    "module_defaults",
    "module_options",
    "recorded_options",
    "resolve_module_configs",
]


class _NoOptions(BaseModel):
    """The options of a module that declares none: every key is unknown."""

    model_config = ConfigDict(extra="forbid")


def module_options(name: str, given: Mapping[str, Any], *, source: str) -> BaseModel | None:
    """Module ``name``'s validated options: its defaults with ``given`` laid over.

    The defaults are the module's packaged preset when it ships one, else those
    of its options model; a module whose every option must be written
    (``every_option_written``) has none, so ``given`` must hold them all.
    ``given`` (a pipeline file's ``params.<name>``, or a run's recorded options)
    is laid over them by the model's schema: a part
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
    defaults = {} if cls.every_option_written else _packaged(name) or {}
    try:
        options = validated(
            model, _laid_over(model, defaults, given), source=source, at=("params", name)
        )
    except OptionsRefused as exc:
        hint = f"The options of {name}: adapt config --params {name}"
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


def module_defaults(name: str) -> dict[str, Any]:
    """Module ``name``'s default options as a file would give them: its packaged
    preset when it ships one (the example of every option, for a module whose
    every option must be written), else every option of its model at its default
    (``{}`` for a module without options)."""
    packaged = _packaged(name)
    if packaged is not None:
        return packaged
    model = registry.get(name).options_class
    return {} if model is None else model().model_dump()


def missing_options(
    name: str, given: Mapping[str, Any]
) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
    """The default options of module ``name`` that ``given`` (a file's
    ``params.<name>``) lacks: ``(path, entries)`` for each mapping of ``given``
    that lacks some, ``path`` leading from ``params.<name>`` to it.

    Only the parts that ``module_options`` lays over key by key are looked into;
    any other option that ``given`` holds replaces its default whole, so
    nothing it lacks is missing. Adding the entries changes no resolved option.
    """
    model = registry.get(name).options_class
    if model is None:
        return []
    return _missing(model, module_defaults(name), given, ())


def _missing(
    model: type[BaseModel], base: Mapping[str, Any], given: Mapping[str, Any], at: tuple[str, ...]
) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
    here = {key: value for key, value in base.items() if key not in given}
    found = [(at, here)] if here else []
    for key, value in given.items():
        part = _options_model(model, key)
        ours = base.get(key)
        if part is not None and isinstance(value, Mapping) and isinstance(ours, Mapping):
            found += _missing(part, ours, value, (*at, key))
    return found


def _packaged(name: str) -> dict[str, Any] | None:
    """Module ``name``'s packaged preset as data; None when it ships none."""
    preset = registry.get(name).options_preset()
    return None if preset is None else dict(yaml.safe_load(preset)[name])


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
