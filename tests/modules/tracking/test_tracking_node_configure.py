# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""TrackingModule.configure: the config tracking runs with, from its options and the run."""

from dataclasses import replace

import pytest

from adapt.modules.tracking.config import TrackingConfig, TrackingOptions
from adapt.modules.tracking.node import TrackingModule
from tests.helpers.shared_config import SHARED

pytestmark = pytest.mark.unit


def test_the_modules_options_model_is_tracking_options():
    assert TrackingModule.options_class is TrackingOptions


def test_configure_returns_a_tracking_config():
    assert isinstance(TrackingModule.configure(TrackingOptions(), SHARED), TrackingConfig)


def test_configure_keeps_every_option():
    options = TrackingOptions(max_speed_ms=30.5, latent_scans=4, polarity=-1, uid_width=12)

    config = TrackingModule.configure(options, SHARED)

    assert {name: getattr(config, name) for name in TrackingOptions.model_fields} == {
        name: getattr(options, name) for name in TrackingOptions.model_fields
    }


def test_configure_takes_the_field_and_the_scan_gap_from_the_runs_shared_values():
    shared = replace(SHARED, tracking_field="velocity", max_scan_gap_minutes=7.5)

    config = TrackingModule.configure(TrackingOptions(), shared)

    assert config.field_var == "velocity"
    assert config.max_tracking_gap_minutes == 7.5
