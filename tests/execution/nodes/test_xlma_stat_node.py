# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""XlmaStatModule declarations + config building."""

import pytest

from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.execution.nodes.xlma_stat import XlmaStatModule
from adapt.modules.xlma_stat.config import XlmaStatConfig
from tests.helpers.pipeline_files import MODULES
from tests.helpers.shared_config import SHARED

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


def test_minute_rows_are_keyed_by_cell_uid_and_minute():
    assert XlmaStatModule.persistence[0].primary_key == ("run_id", "time", "cell_uid")


def test_scan_rows_are_keyed_like_every_per_cell_table():
    """Run, scan and cell label: the key of ``cells_by_scan``, so each cell's
    lightning is read beside its uid, statistics and volume."""
    from adapt.execution.nodes.analysis import AnalysisModule

    cell_stats = AnalysisModule.persistence[0]

    assert XlmaStatModule.persistence[1].primary_key == cell_stats.primary_key
    assert XlmaStatModule.persistence[1].primary_key == ("run_id", "scan_id", "cell_label")


def test_config_has_no_clustering_or_projection_knobs():
    assert set(XlmaStatConfig.model_fields) == {"input_dir", "search_radius_m"}


def test_a_run_configs_module_params_reach_the_module(make_config, tmp_path):
    (tmp_path / "flashes.nc").write_bytes(b"")
    cfg = make_config(
        modules=[*MODULES, "xlma_stat"],
        params={"xlma_stat": {"input_dir": str(tmp_path), "search_radius_m": 750.0}},
    )
    xlma_cfg = resolve_module_configs(cfg, ["xlma_stat"])["xlma_stat_config"]
    assert xlma_cfg == XlmaStatConfig(input_dir=str(tmp_path), search_radius_m=750.0)


def test_configure_without_input_dir_is_refused():
    # Configuring a module means it is about to run: no folder, no run.
    with pytest.raises(ValueError, match=r"params: xlma_stat: \{input_dir"):
        XlmaStatModule.configure(XlmaStatConfig(), SHARED)


def test_configure_with_a_missing_folder_is_refused(tmp_path):
    with pytest.raises(ValueError, match="absent"):
        XlmaStatModule.configure(XlmaStatConfig(input_dir=str(tmp_path / "absent")), SHARED)


def test_a_mistyped_param_is_refused_when_the_run_is_configured(make_config):
    with pytest.raises(ConfigError, match=r"params\.xlma_stat\.search_radius — unrecognized"):
        make_config(modules=[*MODULES, "xlma_stat"], params={"xlma_stat": {"search_radius": 750.0}})


def test_configure_with_a_folder_holding_no_netcdf_is_refused(tmp_path):
    (tmp_path / "LYLOUT_240617_180000_0600.dat").write_text("raw ascii")

    with pytest.raises(ValueError, match=r"\*\.nc"):
        XlmaStatModule.configure(XlmaStatConfig(input_dir=str(tmp_path)), SHARED)


def test_configure_returns_the_options_of_a_folder_with_flash_sorted_files(tmp_path):
    (tmp_path / "flashes.nc").write_bytes(b"")
    options = XlmaStatConfig(input_dir=str(tmp_path))

    assert XlmaStatModule.configure(options, SHARED) == options
