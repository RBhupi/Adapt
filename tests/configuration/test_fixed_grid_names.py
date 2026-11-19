# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The grid's coordinate names and the label grid's name are fixed by the data contract.

They are constants of ``adapt.contracts``: no pipeline file sets them, no module
config carries them, and a run does not record them.
"""

from typing import Any

import pytest
import yaml

from adapt.configuration.pipeline_file import PipelineFile, load_pipeline_file
from adapt.configuration.pipeline_run import configured_modules, run_config
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.contracts import config_key
from adapt.persistence.store import init_store

pytestmark = pytest.mark.unit

_MODULES = ["ingest", "detection", "projection", "analysis", "tracking", "cell_volume_stats"]
_FIXED_NAMES = ("labels_var", "z_coord", "y_coord", "x_coord", "time_coord")


def _file(params: dict) -> dict:
    return {
        "run": {"source": "aws_nexrad", "radar": "KTST", "mode": "realtime"},
        "modules": list(_MODULES),
        "params": params,
    }


def _write(tmp_path, params: dict):
    path = tmp_path / "nexrad.yaml"
    path.write_text(yaml.safe_dump(_file(params), sort_keys=False), encoding="utf-8")
    return path


def _run_config(tmp_path) -> Any:
    """The config of a new run of a pipeline file that sets no option."""
    pipeline = PipelineFile.model_validate(_file({}))
    return run_config(pipeline, init_store(tmp_path / "store") / "case.yaml")


def test_a_pipeline_file_cannot_rename_the_grid_coordinates(tmp_path):
    path = _write(tmp_path, {"global": {"coord_names": {"z": "height"}}})

    with pytest.raises(ConfigError) as exc:
        configured_modules(load_pipeline_file(path), path)

    assert "params.global.coord_names — unrecognized option" in str(exc.value)


@pytest.mark.parametrize("option", _FIXED_NAMES)
def test_a_fixed_name_is_not_an_option_of_cell_volume_stats(tmp_path, option):
    path = _write(tmp_path, {"cell_volume_stats": {option: "other"}})

    with pytest.raises(
        ConfigError, match=rf"params\.cell_volume_stats\.{option} — unrecognized option"
    ):
        configured_modules(load_pipeline_file(path), path)


@pytest.mark.parametrize("name", _MODULES)
def test_no_module_config_carries_a_fixed_name(tmp_path, name):
    config = resolve_module_configs(_run_config(tmp_path), [name])[config_key(name)]

    assert [fixed for fixed in _FIXED_NAMES if hasattr(config, fixed)] == []


def test_the_ingest_config_holds_no_radar_name(tmp_path):
    config = resolve_module_configs(_run_config(tmp_path), ["ingest"])[config_key("ingest")]

    assert not hasattr(config, "radar")


def test_a_run_records_only_the_shared_values_under_global(tmp_path):
    recorded = _run_config(tmp_path).model_dump()["global_"]

    assert set(recorded) == {"z_level", "max_scan_gap_minutes", "tracking_field"}
