import pytest
from pydantic import ValidationError

from adapt.configuration.schemas.user import UserConfig

pytestmark = pytest.mark.unit


def test_uppercase_keys_are_handled():
    raw = {
        "MODE": "historical",
        "RADAR_ID": "KHTX",
        "MIN_CELLSIZE_GRIDPOINT": 40,
        "BASE_DIR": "/tmp/adapt_out",
    }

    user = UserConfig.model_validate(raw)

    assert user.mode == "historical"
    assert user.radar == "KHTX"
    assert user.min_cellsize_gridpoint == 40
    assert user.base_dir == "/tmp/adapt_out"


def test_unknown_top_level_key_is_rejected():
    # No silent drops: a mistyped or retired top-level key fails loudly.
    with pytest.raises(ValidationError, match="UNKNOWN_LEGACY"):
        UserConfig.model_validate({"MODE": "realtime", "UNKNOWN_LEGACY": 12345})


@pytest.mark.parametrize("key", ["extensions", "only_modules", "exclude_modules"])
def test_retired_module_selection_keys_are_rejected(key):
    with pytest.raises(ValidationError, match=key):
        UserConfig.model_validate({key: ["x"]})


@pytest.mark.parametrize(
    "section", ["reader", "tracker", "visualization", "output", "logging", "processor"]
)
def test_sections_without_typed_fields_still_pass_through(section):
    overrides = UserConfig.model_validate({section: {"k": 1}}).to_internal_overrides()

    assert overrides[section] == {"k": 1}
