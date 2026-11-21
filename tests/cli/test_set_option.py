# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt run NAME --set ...`` and ``adapt graph NAME --set ...``: an option given
on the command line is laid over the pipeline file's."""

import pytest
from tests.helpers.pipeline_files import MODULES, TARGET_SELECTION, write_pipeline

from adapt.api.store_client import StoreClient
from adapt.configuration.pipeline_file import load_pipeline_file
from adapt.configuration.pipeline_run import run_config

pytestmark = pytest.mark.unit

_REALTIME = {"source": "aws_nexrad", "radar": "KTST", "mode": "realtime"}
_NO_BAND = "params.arm_radar_comm.extent.vertical_band"


@pytest.fixture
def store(tmp_path, adapt, monkeypatch, capsys):
    """An initialized store as the current folder."""
    root = tmp_path / "store"
    adapt("init", str(root))
    monkeypatch.chdir(root)
    capsys.readouterr()
    return root


def test_a_wrong_value_set_for_the_graph_is_refused_as_the_run_would(store, adapt, capsys):
    path = write_pipeline(store, "case", run=_REALTIME)

    assert adapt("graph", "case", "--set", "params.detection.h_maxima=-1") == 2

    lines = capsys.readouterr().err.splitlines()
    assert lines[0] == (
        f"Invalid configuration (pipeline file {path} with --set params.detection.h_maxima=-1):"
    )
    assert lines[1].startswith("  • params.detection.h_maxima — value -1 is out of range")


def test_an_option_set_for_the_graph_changes_what_a_module_reads(store, adapt, capsys):
    """arm_radar_comm with a fixed vertical band needs no cell_volume_stats."""
    modules = [*MODULES[:-1], "target_selection", "arm_radar_comm"]
    params = {
        "target_selection": TARGET_SELECTION,
        "arm_radar_comm": {
            "radar": {"scan_pattern": "ppi_sector", "user_id_env": "ADAPT_TEST_USER"}
        },
    }
    write_pipeline(store, "case", run=_REALTIME, modules=modules, params=params)

    assert adapt("graph", "case") == 2  # the cells' measured extent has no producer
    capsys.readouterr()
    band = f"{_NO_BAND}={{altitude_min_m: 500.0, altitude_max_m: 9000.0}}"

    assert adapt("graph", "case", "--set", band) == 0
    assert "arm_radar_comm" in capsys.readouterr().out


def test_a_source_option_set_on_the_command_line_reaches_the_run(store):
    path = write_pipeline(store, "case", run=_REALTIME)

    config = run_config(load_pipeline_file(path, ["params.aws_nexrad.poll_interval_sec=30"]), path)

    assert config.source_params is not None
    assert config.source_params["poll_interval_sec"] == 30


def test_a_setting_of_the_removed_downloader_section_gets_its_refusal(store, adapt, capsys):
    write_pipeline(store, "case", run=_REALTIME)

    assert adapt("graph", "case", "--set", "params.downloader.poll_interval_sec=30") == 2

    told = capsys.readouterr().err
    assert "params.downloader is no longer a section" in told
    assert "poll_interval_sec → params.aws_nexrad.poll_interval_sec" in told


def test_set_may_be_given_more_than_once(store, adapt, capsys):
    write_pipeline(store, "case", run=_REALTIME)

    code = adapt(
        "graph",
        "case",
        "--set",
        "params.detection.h_maxima=-1",
        "--set",
        "params.tracking.max_speed_ms=0",
    )

    assert code == 2
    told = capsys.readouterr().err
    assert "params.detection.h_maxima" in told
    assert "params.tracking.max_speed_ms" in told


def test_a_setting_outside_params_is_refused(store, adapt, capsys):
    write_pipeline(store, "case", run=_REALTIME)

    assert adapt("run", "case", "--set", "run.radar=KLOT") == 2

    assert "--set run.radar=KLOT" in capsys.readouterr().err
    with StoreClient(store) as client:
        assert client.runs() == []  # nothing started
