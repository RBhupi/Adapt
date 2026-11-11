# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Every option ``adapt config --show`` can print carries a description.

``--show`` prints a ``ParamConfig`` section, a phase >= 3 module's own
params, or a consumer's section, each option with its ``Field`` description
as the comment that says what it means.
"""

from typing import get_args

import pytest
from pydantic import BaseModel

from adapt.configuration.schemas.param import ParamConfig
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import catalog_module_names

pytestmark = pytest.mark.unit

# Dead options, flagged ``# OBSOLETE`` in their schema instead of described.
OBSOLETE = {
    "downloader.output_dir",
    "tracker.cell_uid.alphabet",
    "visualization.enabled",
    "output.compression",
}


def _models_in(annotation) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [model for arg in get_args(annotation) for model in _models_in(arg)]


def _undescribed(model: type[BaseModel], prefix: str, skip=frozenset()) -> list[str]:
    missing = []
    for name, field in model.model_fields.items():
        if name in skip:
            continue
        path = f"{prefix}.{field.alias or name}"
        if not (field.description or "").strip():
            missing.append(path)
        for nested in _models_in(field.annotation):
            missing.extend(_undescribed(nested, path))
    return missing


def _showable() -> list[tuple[str, type[BaseModel], frozenset[str]]]:
    sections = [
        (field.alias or name, model, frozenset())
        for name, field in ParamConfig.model_fields.items()
        for model in _models_in(field.annotation)
    ]
    # A module without params sections owns its options: its config class describes them.
    modules = [
        (name, cls.config_class, cls.injected_global_fields)
        for name in catalog_module_names()
        if not (cls := registry.get(name)).param_sections
    ]
    return [*sections, *modules]


def test_every_user_facing_option_is_described():
    missing = [
        path
        for prefix, model, skip in _showable()
        for path in _undescribed(model, prefix, skip)
        if path not in OBSOLETE
    ]
    assert missing == []
