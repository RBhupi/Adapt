# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""global_.tracking_field — the role knob: which canonical field the core
detects, projects, and tracks on. A naming knob it is not (names are
canonicalized at ingest via ingest.field_map)."""

import pytest

from adapt.configuration.schemas.errors import ConfigError

pytestmark = pytest.mark.unit


def test_tracking_field_default(internal_config):
    assert internal_config.global_.tracking_field == "reflectivity"


def test_var_names_section_is_gone(make_config):
    with pytest.raises(ConfigError, match=r"params\.global\.var_names — unrecognized option"):
        make_config(params={"global": {"var_names": {"reflectivity": "x"}}})


def test_a_pipeline_file_can_set_the_tracking_field(make_config):
    cfg = make_config(params={"global": {"tracking_field": "velocity"}})
    assert cfg.global_.tracking_field == "velocity"  # analysis measures velocity by default
