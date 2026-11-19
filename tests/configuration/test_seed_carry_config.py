# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Seed-carry configuration: one frame by default, bounded, and needs projection."""

import pytest
from pydantic import ValidationError

from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import resolve_enabled_modules, scan_graph
from adapt.modules.detection.config import DetectionOptions

pytestmark = pytest.mark.unit


def _wire(cfg) -> list:
    """The per-scan graph of ``cfg``'s modules, each configured from ``cfg`` (as a run does)."""
    modules = resolve_enabled_modules(registry.create_modules(), cfg.modules)
    return scan_graph(modules, resolve_module_configs(cfg, [m.name for m in modules]))


def test_seed_carry_defaults_to_one_frame():
    """One integer governs the carry: 1 means only from the preceding frame."""
    seg = DetectionOptions()
    assert seg.seed_carry_frames == 1
    assert seg.seed_carry_min_separation == 5
    assert seg.carried_exempt_size_filter is False


def test_seed_carry_can_be_disabled():
    assert DetectionOptions(seed_carry_frames=0).seed_carry_frames == 0


@pytest.mark.parametrize("field", ["seed_carry_frames", "seed_carry_min_separation"])
def test_seed_carry_bounds_below_zero_are_rejected(field):
    with pytest.raises(ValidationError):
        DetectionOptions(**{field: -1})


def test_seed_carry_min_separation_below_one_is_rejected():
    with pytest.raises(ValidationError):
        DetectionOptions(seed_carry_min_separation=0)


def test_the_modules_config_holds_the_seed_carry_options(make_detection_config):
    det = make_detection_config(seed_carry_frames=4, seed_carry_min_separation=7)
    assert det.seed_carry_frames == 4
    assert det.seed_carry_min_separation == 7


@pytest.mark.parametrize(
    "modules",
    [["ingest", "detection"], ["ingest", "detection", "analysis", "tracking"]],
)
def test_seed_carry_on_without_projection_is_refused_when_the_modules_are_wired(
    make_config, modules
):
    with pytest.raises(ConfigError, match="list 'projection'"):
        make_config(modules=modules, params={"detection": {"seed_carry_frames": 1}})


def test_seed_carry_off_needs_no_projection(make_config):
    cfg = make_config(
        modules=["ingest", "detection"], params={"detection": {"seed_carry_frames": 0}}
    )
    assert {node.name for node in _wire(cfg)} == {"ingest", "detection"}


def test_seed_carry_on_is_wired_when_projection_is_listed(make_config):
    cfg = make_config(
        modules=["ingest", "detection", "projection"],
        params={"detection": {"seed_carry_frames": 1}},
    )
    assert {node.name for node in _wire(cfg)} == {"ingest", "detection", "projection"}
