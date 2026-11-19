# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The radar-processing modules read an option's text without the spaces around it.

A name written ``" velocity "`` in a pipeline file is the name ``velocity``, for
every text option of ingest, detection, projection, analysis and tracking, in
their nested parts too. A choice is not text: it must be one of its values as
written.
"""

from typing import get_args

import pytest
from pydantic import BaseModel

from adapt.configuration.pipeline_file import PipelineFile
from adapt.configuration.pipeline_run import configured_modules
from adapt.configuration.schemas.errors import ConfigError
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import catalog_module_names

pytestmark = pytest.mark.unit

_MODULES = ("ingest", "detection", "projection", "analysis", "tracking")


def _options_models(model: type[BaseModel]) -> list[type[BaseModel]]:
    """``model`` and every model of options nested in it."""
    found = [model]
    for field in model.model_fields.values():
        for part in (field.annotation, *get_args(field.annotation)):
            if isinstance(part, type) and issubclass(part, BaseModel):
                found.extend(_options_models(part))
    return found


@pytest.mark.parametrize("module", _MODULES)
def test_every_part_of_the_modules_options_trims_text(module):
    catalog_module_names()  # registers every catalog module
    options_class = registry.get(module).options_class

    keep_spaces = [
        model.__name__
        for model in _options_models(options_class)
        if model.model_config.get("str_strip_whitespace") is not True
    ]

    assert not keep_spaces


@pytest.mark.parametrize(
    ("module", "option", "padded"),
    [
        ("projection", "method", " adapt_default "),
        ("detection", "method", " threshold "),
        ("ingest", "roi_func", " dist_beam "),
    ],
)
def test_a_choice_given_with_spaces_around_it_is_refused(tmp_path, module, option, padded):
    pipeline = PipelineFile.model_validate(
        {
            "run": {"source": "aws_nexrad", "radar": "KBOX", "mode": "realtime"},
            "modules": list(_MODULES),
            "params": {module: {option: padded}},
        }
    )

    with pytest.raises(ConfigError, match=rf"params\.{module}\.{option} — Input should be "):
        configured_modules(pipeline, tmp_path / "case.yaml")
