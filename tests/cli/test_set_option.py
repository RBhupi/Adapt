# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt run NAME --set ...`` and ``adapt graph NAME --set ...``: an option given
on the command line is laid over the pipeline file's."""

import pytest
from tests.helpers.pipeline_files import MODULES, TARGET_SELECTION, write_pipeline

from adapt.api.store_client import StoreClient

pytestmark = pytest.mark.unit

_REALTIME = {"source": "aws_nexrad", "radar": "KTST", "mode": "realtime"}
_NO_BAND = "params.radar_command.extent.vertical_band"


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
    """radar_command with a fixed vertical band needs no cell_volume_stats."""
    modules = [*MODULES[:-1], "target_selection", "radar_command"]
    params = {
        "target_selection": TARGET_SELECTION,
        "radar_command": {"radar": {"scan_pattern": "ppi_sector", "user_id": "operator"}},
    }
    write_pipeline(store, "case", run=_REALTIME, modules=modules, params=params)

    assert adapt("graph", "case") == 2  # the cells' measured extent has no producer
    capsys.readouterr()
    band = f"{_NO_BAND}={{altitude_min_m: 500.0, altitude_max_m: 9000.0}}"

    assert adapt("graph", "case", "--set", band) == 0
    assert "radar_command" in capsys.readouterr().out


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
