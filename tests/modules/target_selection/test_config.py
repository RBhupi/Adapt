# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The target_selection section — fail loud on missing or unknown keys."""

import pytest
import yaml
from pydantic import ValidationError

from adapt.modules.target_selection.config import TSEConfig, config_hash

pytestmark = pytest.mark.unit

VALID_YAML = """\
entry:
  gates:
    - {field: n_scans, op: ge, value: 3}
    - {field: radar_reflectivity_max, op: gt, value: 45}
    - {field: age_seconds, op: ge, value: 600}
score:
  weights:
    field_max: 1.0
    cell_area_sqkm: 0.05
    growth_rate_sqkm_per_min: 2.0
  site_preference:
    projection_steps: 5
    sites:
      - {name: sgp_c1, lat: 36.607, lon: -97.488, radius_km: 20.0, bonus: 10.0}
hold:
  gates:
    - {field: radar_reflectivity_max, op: gt, value: 40}
switch:
  allow: true
  margin: 5.0
duration:
  max_minutes: 30
snapshot:
  growth_window_scans: 4
"""


def _load(text):
    return TSEConfig.model_validate(yaml.safe_load(text))


def test_load_valid_section():
    cfg = _load(VALID_YAML)
    assert [g.field for g in cfg.entry.gates] == [
        "n_scans",
        "radar_reflectivity_max",
        "age_seconds",
    ]
    assert cfg.entry.gates[0].op == "ge"
    assert cfg.entry.gates[0].value == 3.0
    assert cfg.entry.gates[1].op == "gt"
    assert cfg.score.weights["cell_area_sqkm"] == 0.05
    assert cfg.score.site_preference.sites[0].radius_km == 20.0
    assert cfg.switch.margin == 5.0
    assert cfg.duration.max_minutes == 30.0
    assert cfg.snapshot.growth_window_scans == 4


def test_config_is_frozen():
    cfg = _load(VALID_YAML)
    with pytest.raises(ValidationError):
        cfg.switch = None  # type: ignore[assignment]


def test_missing_key_raises():
    text = VALID_YAML.replace("  margin: 5.0\n", "")
    with pytest.raises(ValidationError):
        _load(text)


def test_unknown_key_raises():
    with pytest.raises(ValidationError):
        _load(VALID_YAML + "radar: kdix\n")


def test_output_paths_are_not_configurable():
    # Where decisions are written is the launcher's fixed layout, not a rule.
    with pytest.raises(ValidationError, match="output"):
        _load(VALID_YAML + "output:\n  decision_log: /data/tse/decisions.jsonl\n")


def test_a_weight_must_be_a_number():
    text = VALID_YAML.replace("    cell_area_sqkm: 0.05\n", "    cell_area_sqkm: high\n")
    with pytest.raises(ValidationError, match="cell_area_sqkm"):
        _load(text)


def test_unknown_operator_raises():
    text = VALID_YAML.replace("op: ge", "op: approximately")
    with pytest.raises(ValidationError):
        _load(text)


def test_hold_gates_are_explicit_and_may_be_empty():
    # Empty: nothing ends a target but loss of track or the duration cap (the
    # growing_isolated preset relies on it). Absent: refused, like every other part.
    text = VALID_YAML.replace(
        "hold:\n  gates:\n    - {field: radar_reflectivity_max, op: gt, value: 40}\n",
        "hold:\n  gates: []\n",
    )
    assert _load(text).hold.gates == ()
    with pytest.raises(ValidationError, match="hold"):
        _load(
            VALID_YAML.replace(
                "  gates:\n    - {field: radar_reflectivity_max, op: gt, value: 40}\n",
                "  gates: []\n",
            ).replace("hold:\n  gates: []\n", "hold: {}\n")
        )


def test_no_duration_cap_is_null_not_absent():
    assert (
        _load(
            VALID_YAML.replace("  max_minutes: 30\n", "  max_minutes: null\n")
        ).duration.max_minutes
        is None
    )
    with pytest.raises(ValidationError, match="max_minutes"):
        _load(VALID_YAML.replace("  max_minutes: 30\n", "  max_minutes: 0\n"))


def test_track_summary_defaults_to_the_scan_being_replayed():
    assert _load(VALID_YAML).snapshot.track_summary == "at_scan"


def test_track_summary_end_of_run_selects_the_oracle():
    text = VALID_YAML.replace(
        "  growth_window_scans: 4\n",
        "  growth_window_scans: 4\n  track_summary: end_of_run\n",
    )
    assert _load(text).snapshot.track_summary == "end_of_run"


def test_unknown_track_summary_raises():
    text = VALID_YAML.replace(
        "  growth_window_scans: 4\n", "  growth_window_scans: 4\n  track_summary: future\n"
    )
    with pytest.raises(ValidationError, match="track_summary"):
        _load(text)


def test_config_hash_is_the_same_for_the_same_rules():
    assert config_hash(_load(VALID_YAML)) == config_hash(_load(VALID_YAML))


def test_config_hash_changes_with_a_rule():
    changed = VALID_YAML.replace("margin: 5.0", "margin: 6.0")
    assert config_hash(_load(VALID_YAML)) != config_hash(_load(changed))
