# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Pipeline files for the ``adapt`` command tests, and valid module options."""

from pathlib import Path

import certifi
import yaml

MODULES = ["ingest", "detection", "projection", "analysis", "tracking", "cell_volume_stats"]

# A target selection rule: any cell with a 45 dBZ core, kept while it has one.
TARGET_SELECTION = {
    "entry": {"gates": [{"field": "radar_reflectivity_max", "op": "ge", "value": 45.0}]},
    "score": {
        "weights": {"field_max": 1.0, "cell_area_sqkm": 0.05, "growth_rate_sqkm_per_min": 2.0},
        "site_preference": {"projection_steps": 5, "sites": []},
    },
    "hold": {"gates": [{"field": "radar_reflectivity_max", "op": "ge", "value": 45.0}]},
    "switch": {"allow": True, "margin": 5.0},
    "duration": {"max_minutes": 30.0},
    "snapshot": {"growth_window_scans": 2, "trend_fields": []},
}
# Radar command options (a dry run: no api: part).
RADAR_COMMAND = {
    "radar": {"scan_pattern": "ppi_sector", "user_id": "operator"},
    "extent": {"horizontal_buffer_km": 1.0},
}
# The api: part names the environment variables; ``export_api`` sets them.
API = {
    "base_url_env": "ADAPT_TEST_URL",
    "api_key_env": "ADAPT_TEST_KEY",
    "ca_file_env": "ADAPT_TEST_CA",
    "timeout_seconds": 5,
}
API_URL = "https://radar.example.test/site"
API_KEY = "a-test-key"


def export_api(monkeypatch, **values: str) -> None:
    """Set the variables ``API`` names: ``API_URL``, ``API_KEY`` and a CA file
    (the installed certifi bundle, a real PEM file on every platform)."""
    exported = {
        "ADAPT_TEST_URL": API_URL,
        "ADAPT_TEST_KEY": API_KEY,
        "ADAPT_TEST_CA": certifi.where(),
    }
    for name, value in (exported | values).items():
        monkeypatch.setenv(name, value)


def write_pipeline(root: Path, name: str, **sections) -> Path:
    """Write pipeline file ``root/NAME.yaml``: a historical KTST run of ``MODULES``
    unless overridden."""
    data = {
        "run": {"source": "aws_nexrad", "radar": "KTST", "mode": "historical"},
        "modules": list(MODULES),
        "params": {},
        **sections,
    }
    path = root / f"{name}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path
