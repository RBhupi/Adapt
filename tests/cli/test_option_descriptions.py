# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Every option ``adapt config --params`` can print carries a description.

``--params`` prints a section of the run config or a module's options model, each
option with its ``Field`` description as the comment that says what it means.
"""

from typing import get_args

import pytest
from pydantic import BaseModel

from adapt.configuration.pipeline_file import param_section_models
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import catalog_module_names

pytestmark = pytest.mark.unit


def _models_in(annotation) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [model for arg in get_args(annotation) for model in _models_in(arg)]


def _undescribed(model: type[BaseModel], prefix: str) -> list[str]:
    missing = []
    for name, field in model.model_fields.items():
        path = f"{prefix}.{field.alias or name}"
        if not (field.description or "").strip():
            missing.append(path)
        for nested in _models_in(field.annotation):
            missing.extend(_undescribed(nested, path))
    return missing


def _showable() -> list[tuple[str, type[BaseModel]]]:
    sections = list(param_section_models().items())
    # A module's options model describes its options.
    modules = [(name, registry.get(name).options_class) for name in catalog_module_names()]
    return [*sections, *modules]


def test_every_user_facing_option_is_described():
    missing = [path for prefix, model in _showable() for path in _undescribed(model, prefix)]
    assert missing == []
