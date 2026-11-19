# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Config validation errors read clearly: which field, what value, and why."""

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from adapt.configuration.schemas.errors import ConfigError, validated
from adapt.configuration.schemas.module_resolver import module_options

pytestmark = pytest.mark.unit


class _Sample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    count: int = Field(ge=0, le=10)


def _refusal(model: type[BaseModel], data: dict) -> str:
    """What the user is told when ``data`` is not a valid ``model``."""
    with pytest.raises(ConfigError) as excinfo:
        validated(model, data, source="nexrad.yaml")
    return str(excinfo.value)


def test_extra_forbidden_names_field_value_and_reason():
    msg = _refusal(_Sample, {"name": "x", "count": 5, "bogus": 1})
    assert "nexrad.yaml" in msg
    assert "bogus" in msg
    assert "unrecognized option" in msg


def test_missing_required_field_is_reported():
    msg = _refusal(_Sample, {"count": 5})
    assert "name" in msg
    assert "required option is missing" in msg


def test_out_of_range_reports_value_and_bound():
    msg = _refusal(_Sample, {"name": "x", "count": 99})
    assert "count" in msg
    assert "99" in msg
    assert "out of range" in msg


def test_validated_raises_configerror_not_validationerror():
    with pytest.raises(ConfigError) as excinfo:
        validated(_Sample, {"name": "x", "count": 5, "bogus": 1}, source="my_source")
    assert "my_source" in str(excinfo.value)
    assert not isinstance(excinfo.value, ValidationError)


def test_a_stale_detection_option_is_reported_with_its_path_and_value():
    """The reported case: a removed 'threshold' among detection's options must name the path."""
    with pytest.raises(ConfigError) as excinfo:
        module_options("detection", {"threshold": 30.0}, source="nexrad.yaml")
    message = str(excinfo.value)
    assert "params.detection.threshold" in message
    assert "30.0" in message


def test_a_union_field_is_reported_once_without_branch_names():
    class _Union(BaseModel):
        poll: int | float

    msg = _refusal(_Union, {"poll": "fast"})

    bullets = [line for line in msg.splitlines() if line.startswith("  •")]
    assert len(bullets) == 1
    assert "poll —" in bullets[0]
    assert ".int" not in msg and ".float" not in msg


def test_a_section_given_as_a_scalar_asks_for_a_mapping_in_plain_words():
    class _Nested(BaseModel):
        run: _Sample

    msg = _refusal(_Nested, {"run": "nope"})

    assert "must be a mapping" in msg
    assert "instance of" not in msg
