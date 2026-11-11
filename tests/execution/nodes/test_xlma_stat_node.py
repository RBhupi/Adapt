# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""XlmaStatModule declarations + config building."""

import pytest
from pydantic import ValidationError

from adapt.execution.nodes.xlma_stat import XlmaStatModule
from adapt.modules.xlma_stat.config import XlmaStatConfig

pytestmark = pytest.mark.unit


def test_module_declares_its_two_tables():
    m = XlmaStatModule()
    assert m.name == "xlma_stat"
    assert {spec.key for spec in m.persistence} == {
        "xlma_stat_minutes_rows",
        "xlma_stat_scan_rows",
    }
    assert m.persistence[0].table == "xlma_stat_minutes"
    assert m.persistence[1].table == "xlma_stat_scan"


def test_module_consumes_geometry_it_never_computes():
    """The lightning module reads the scan's analysis dataset; it owns no projection inputs."""
    assert XlmaStatModule.inputs == ["xlma_stat_config", "analysis_ds", "scan_time"]


def test_output_tables_are_keyed_by_run_and_time():
    m = XlmaStatModule()
    assert m.persistence[0].primary_key == (
        "run_id",
        "time",
        "cell_uid",
    )
    assert m.persistence[1].primary_key == (
        "run_id",
        "scan_time",
        "cell_uid",
    )


def test_config_has_no_clustering_or_projection_knobs():
    assert set(XlmaStatConfig.model_fields) == {"input_dir", "search_radius_m"}


def test_build_config_reads_module_params(make_config, tmp_path):
    (tmp_path / "flashes.nc").write_bytes(b"")
    cfg = make_config(
        module_params={"xlma_stat": {"input_dir": str(tmp_path), "search_radius_m": 750.0}}
    )
    xlma_cfg = XlmaStatModule.build_config(cfg)
    assert isinstance(xlma_cfg, XlmaStatConfig)
    assert xlma_cfg.input_dir == str(tmp_path)
    assert xlma_cfg.search_radius_m == 750.0


def test_build_config_without_input_dir_is_refused(make_config):
    # Building a module's config means it is about to run: no folder, no run.
    cfg = make_config(module_params={"xlma_stat": {}})
    with pytest.raises(ValueError, match=r"params: xlma_stat: \{input_dir"):
        XlmaStatModule.build_config(cfg)


def test_build_config_with_a_missing_folder_is_refused(make_config, tmp_path):
    cfg = make_config(module_params={"xlma_stat": {"input_dir": str(tmp_path / "absent")}})
    with pytest.raises(ValueError, match="absent"):
        XlmaStatModule.build_config(cfg)


def test_build_config_rejects_a_mistyped_param(make_config):
    cfg = make_config(module_params={"xlma_stat": {"search_radius": 750.0}})
    with pytest.raises(ValidationError, match=r"search_radius\s+Extra inputs"):
        XlmaStatModule.build_config(cfg)


def test_build_config_with_a_folder_holding_no_netcdf_is_refused(make_config, tmp_path):
    (tmp_path / "LYLOUT_240617_180000_0600.dat").write_text("raw ascii")
    cfg = make_config(module_params={"xlma_stat": {"input_dir": str(tmp_path)}})

    with pytest.raises(ValueError, match=r"\*\.nc"):
        XlmaStatModule.build_config(cfg)
