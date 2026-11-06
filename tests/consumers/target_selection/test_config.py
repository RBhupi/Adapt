# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""YAML config loading — fail loud on missing or unknown keys."""

import pytest
from pydantic import ValidationError

from adapt.consumers.target_selection.config import load_config

pytestmark = pytest.mark.unit

VALID_YAML = """\
target_selection:
  candidate:
    gates:
      - {field: n_scans, op: ge, value: 3}
      - {field: radar_reflectivity_max, op: gt, value: 45}
    min_age_seconds: 600
  priority:
    weights:
      field: 1.0
      area: 0.05
      growth_rate: 2.0
  site_preference:
    projection_steps: 5
    sites:
      - {name: sgp_c1, lat: 36.607, lon: -97.488, radius_km: 20.0, bonus: 10.0}
  selection:
    switch_margin: 5.0
    max_observation_seconds: 1800
  snapshot:
    growth_window_scans: 4
  output:
    jsonl_path: /data/tse/selections.jsonl
"""


def _write(tmp_path, text):
    path = tmp_path / "tse.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_valid_yaml(tmp_path):
    cfg = load_config(_write(tmp_path, VALID_YAML))
    assert [g.field for g in cfg.candidate.gates] == ["n_scans", "radar_reflectivity_max"]
    assert cfg.candidate.gates[0].op == "ge"
    assert cfg.candidate.gates[0].value == 3.0
    assert cfg.candidate.gates[1].op == "gt"
    assert cfg.priority.weights.area == 0.05
    assert cfg.site_preference.sites[0].radius_km == 20.0
    assert cfg.selection.switch_margin == 5.0
    assert cfg.snapshot.growth_window_scans == 4
    assert cfg.output.jsonl_path == "/data/tse/selections.jsonl"


def test_config_is_frozen(tmp_path):
    cfg = load_config(_write(tmp_path, VALID_YAML))
    with pytest.raises(ValidationError):
        cfg.selection = None  # type: ignore[assignment]


def test_missing_key_raises(tmp_path):
    text = VALID_YAML.replace("    switch_margin: 5.0\n", "")
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, text))


def test_unknown_key_raises(tmp_path):
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, VALID_YAML + "  radar: kdix\n"))


def test_unknown_weight_raises(tmp_path):
    text = VALID_YAML.replace("      area: 0.05\n", "      area: 0.05\n      vorticity: 2.0\n")
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, text))


def test_unknown_operator_raises(tmp_path):
    text = VALID_YAML.replace("op: ge", "op: approximately")
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, text))


def test_empty_yaml_raises(tmp_path):
    with pytest.raises(ValueError, match="not a mapping"):
        load_config(_write(tmp_path, ""))


def test_missing_section_raises(tmp_path):
    text = "radar_command:\n  radar: {command: c, pattern: p, user: u}\n"
    with pytest.raises(ValueError, match="target_selection"):
        load_config(_write(tmp_path, text))


def test_sibling_section_is_ignored(tmp_path):
    text = VALID_YAML + "radar_command:\n  not_a_tse_key: 1\n"
    cfg = load_config(_write(tmp_path, text))
    assert cfg.selection.switch_margin == 5.0


def test_output_optional(tmp_path):
    text = VALID_YAML.split("output:")[0]
    cfg = load_config(_write(tmp_path, text))
    assert cfg.output.jsonl_path is None


def test_empty_hold_gates_load_as_no_hold_gates_not_as_absent(tmp_path):
    # Absent: the entry gates also decide holding. Empty: nothing ends a target but
    # loss of track or the duration cap (the growing_isolated preset relies on it).
    text = VALID_YAML.replace(
        "    min_age_seconds: 600\n", "    min_age_seconds: 600\n    hold_gates: []\n"
    )
    assert load_config(_write(tmp_path, text)).candidate.hold_gates == ()
    assert load_config(_write(tmp_path, VALID_YAML)).candidate.hold_gates is None
