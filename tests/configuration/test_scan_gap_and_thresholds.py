# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""One scan-gap bound, and independent split/merge thresholds."""

import pytest

from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.modules.projection.config import ProjectionOptions
from adapt.modules.tracking.config import TrackingOptions

pytestmark = pytest.mark.unit


def test_scan_gap_lives_only_on_global(internal_config):
    """The per-module gap fields are gone, so the stages cannot disagree."""
    assert internal_config.global_.max_scan_gap_minutes == 10.0
    assert "max_time_interval_minutes" not in ProjectionOptions.model_fields
    assert "max_tracking_gap_minutes" not in TrackingOptions.model_fields


def test_every_stage_reads_the_same_gap(make_config):
    cfg = make_config(params={"global": {"max_scan_gap_minutes": 7.5}})
    assert cfg.global_.max_scan_gap_minutes == 7.5
    configs = resolve_module_configs(cfg, ["projection", "tracking"])
    assert configs["projection_config"].max_time_interval_minutes == 7.5
    assert configs["tracking_config"].max_tracking_gap_minutes == 7.5


def test_split_and_merge_thresholds_are_independent(make_config):
    defaults = TrackingOptions()
    assert defaults.split_overlap_threshold == 0.65
    assert defaults.merge_overlap_threshold == 0.7

    cfg = make_config(params={"tracking": {"split_overlap_threshold": 0.5}})
    tracking = resolve_module_configs(cfg, ["tracking"])["tracking_config"]
    assert tracking.split_overlap_threshold == 0.5
    assert tracking.merge_overlap_threshold == 0.7
