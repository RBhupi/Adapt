# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tracker v2 parameters: defaults, overrides reach the tracker, bad values fail loudly."""

import pytest

from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.module_resolver import module_options, resolve_module_configs
from adapt.modules.tracking.config import TrackingOptions

pytestmark = pytest.mark.unit


def test_physical_limits_and_rule_defaults():
    t = TrackingOptions()
    assert (t.max_speed_ms, t.max_acceleration_ms2) == (40.0, 0.04)
    assert (t.growth_max_km, t.growth_size_km) == (2.0, 12.0)
    assert (t.residual_weight, t.heading_weight) == (0.25, 1.0)
    assert (t.latent_scans, t.identity_intensity_weight, t.identity_score_margin) == (2, 1.0, 0.33)
    assert (t.cell_threshold, t.polarity) == (30.0, 1)


def test_rule_thresholds_are_in_their_valid_ranges():
    t = TrackingOptions()
    assert 0.0 < t.max_overlap_mismatch <= 1.0
    assert t.max_link_cost > 0.0
    assert 0.0 < t.split_overlap_threshold <= 1.0
    assert 0.0 < t.merge_overlap_threshold <= 1.0


def test_override_reaches_the_tracker(make_config):
    given = {"max_link_cost": 1.5, "latent_scans": 3, "polarity": -1}
    cfg = resolve_module_configs(make_config(params={"tracking": given}), ["tracking"])[
        "tracking_config"
    ]
    assert (cfg.max_link_cost, cfg.latent_scans, cfg.polarity) == (1.5, 3, -1)


def test_polarity_must_be_a_sign():
    with pytest.raises(ConfigError):
        module_options("tracking", {"polarity": 2}, source="a test")


def test_negative_latency_is_rejected():
    with pytest.raises(ConfigError):
        module_options("tracking", {"latent_scans": -1}, source="a test")
