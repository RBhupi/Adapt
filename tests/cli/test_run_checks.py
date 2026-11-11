# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt run NAME``: everything is checked before anything starts.

The current folder must be a store holding ``NAME.yaml``; every listed module
must be configured and have every input it reads; a file over a stored run
needs ``--run-id`` of a run of its radar. A failed check exits non-zero with a
message and starts nothing.
"""

import os

import pytest
from tests.helpers import radar_store
from tests.helpers.pipeline_files import API, MODULES, RADAR_COMMAND, write_pipeline

from adapt.api import StoreClient

pytestmark = pytest.mark.unit

RADAR = [*MODULES, "target_selection", "radar_command"]
STORED = {"source": "stored_run", "radar": "KTST", "mode": "historical"}


@pytest.fixture
def store(tmp_path, adapt, monkeypatch, capsys):
    """An initialized store as the current folder (with its nexrad.yaml)."""
    root = tmp_path / "case"
    adapt("init", str(root))
    monkeypatch.chdir(root)
    capsys.readouterr()
    return root


@pytest.fixture
def live(store, tmp_path):
    """Write a pipeline file over a local folder holding one unreadable scan: were
    a check to let it through, the run would fail at once (never on the network)."""
    folder = tmp_path / "scans"
    folder.mkdir()
    (folder / "KTST20240518_120000_V06").write_bytes(b"x" * 6000)
    run = {
        "source": "local_directory",
        "radar": "KTST",
        "mode": "historical",
        "start_time": "2024-05-18T12:00:00Z",
        "end_time": "2024-05-18T13:00:00Z",
        "source_dir": str(folder),
    }

    def _write(name: str = "case", **sections):
        return write_pipeline(store, name, run={**run, **sections.pop("run", {})}, **sections)

    return _write


@pytest.fixture
def stored(tmp_path, monkeypatch):
    """A store holding the completed run 'run-1' of radar KTST, as the current folder."""
    root = radar_store.build_store(tmp_path / "built", volume_stats=True).root
    monkeypatch.chdir(root)
    return root


def _no_runs(root) -> bool:
    with StoreClient(root) as client:
        return client.runs() == [] and client.collections() == []


def test_a_folder_that_is_not_a_store_is_refused(tmp_path, adapt, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_pipeline(tmp_path, "case")

    assert adapt("run", "case") == 1

    err = capsys.readouterr().err
    assert str(tmp_path.resolve()) in err
    assert "adapt init" in err


def test_a_missing_pipeline_file_is_named_with_a_file_to_start_from(store, adapt, capsys):
    assert adapt("run", "kdix") == 2

    err = capsys.readouterr().err
    assert str(store / "kdix.yaml") in err
    assert "cp nexrad.yaml kdix.yaml" in err


def test_a_missing_template_file_names_the_command_writing_it(store, adapt, capsys):
    (store / "nexrad.yaml").unlink()

    assert adapt("run", "nexrad") == 2

    assert "adapt config nexrad" in capsys.readouterr().err


def test_a_name_given_with_its_suffix_points_at_the_bare_name(store, adapt, capsys):
    assert adapt("run", "nexrad.yaml") == 2

    assert "adapt run nexrad" in capsys.readouterr().err


def test_a_pipeline_name_is_a_bare_file_stem(store, adapt, capsys):
    assert adapt("run", "sub/case") == 2

    assert "sub/case" in capsys.readouterr().err


def test_radar_command_needs_target_selection_in_the_same_file(store, live, adapt, capsys):
    live(modules=[*MODULES, "radar_command"])

    assert adapt("run", "case") == 2

    assert "list 'target_selection' too" in capsys.readouterr().err
    assert _no_runs(store)


def test_a_wrong_option_of_a_module_is_named(store, live, adapt, capsys):
    live(modules=RADAR, params={"radar_command": {"radar": {"scan_patern": "ppi"}}})

    assert adapt("run", "case") == 2

    assert "scan_patern" in capsys.readouterr().err
    assert _no_runs(store)


def test_a_run_that_sends_needs_the_radar_api_key_before_it_starts(
    store, live, adapt, monkeypatch, capsys
):
    monkeypatch.delenv("ADAPT_TEST_KEY", raising=False)
    live(modules=RADAR, params={"radar_command": {**RADAR_COMMAND, "api": API}})

    assert adapt("run", "case") == 2

    assert "ADAPT_TEST_KEY" in capsys.readouterr().err
    assert _no_runs(store)


def test_a_live_run_refuses_while_another_pipeline_runs(store, live, adapt, home, capsys):
    (home / ".adapt").mkdir()
    (home / ".adapt" / "pipeline.pid").write_text(str(os.getpid()))  # our PID is alive
    live()

    assert adapt("run", "case") == 1

    assert "already running" in capsys.readouterr().out
    assert _no_runs(store)


def test_a_live_run_refuses_a_malformed_pid_file(store, live, adapt, home, capsys):
    pid = home / ".adapt" / "pipeline.pid"
    pid.parent.mkdir()
    pid.write_text("not-a-pid")
    live()

    assert adapt("run", "case") == 1

    assert str(pid) in capsys.readouterr().out
    assert _no_runs(store)


def test_an_unknown_source_lists_the_registered_ones(store, live, adapt, capsys):
    live(run={"source": "aws_nexard", "source_dir": None})

    assert adapt("run", "case") == 2

    err = capsys.readouterr().err
    assert "run.source 'aws_nexard'" in err
    assert "aws_nexrad" in err
    assert "local_directory" in err
    assert _no_runs(store)


def test_a_module_missing_its_input_is_refused_before_the_run(store, live, adapt, capsys):
    live(
        modules=["ingest", "detection", "analysis", "tracking"],
        params={"segmenter": {"seed_carry_frames": 0}},
    )

    assert adapt("run", "case") == 2

    assert "list 'projection' too" in capsys.readouterr().err
    assert _no_runs(store)


def test_a_module_missing_its_options_is_refused_before_the_run(store, live, adapt, capsys):
    live(modules=[*MODULES, "xlma_stat"])

    assert adapt("run", "case") == 2

    assert "input_dir" in capsys.readouterr().err
    assert _no_runs(store)


def test_a_malformed_run_id_is_refused_before_the_run(store, live, adapt, capsys):
    live()

    assert adapt("run", "case", "--run-id", "bogus") == 2

    assert "YYYYMONDD-HHMM-RADAR" in capsys.readouterr().err
    assert _no_runs(store)


def test_a_file_over_a_stored_run_needs_a_run_id(stored, adapt, capsys):
    write_pipeline(stored, "again", run=STORED, modules=["target_selection"])

    assert adapt("run", "again") == 2

    assert "--run-id" in capsys.readouterr().err


def test_an_unknown_run_id_lists_the_stores_runs_of_the_radar(stored, adapt, capsys):
    write_pipeline(stored, "again", run=STORED, modules=["target_selection"])

    assert adapt("run", "again", "--run-id", "2026OCT22-0000-KTST") == 2

    err = capsys.readouterr().err
    assert "2026OCT22-0000-KTST" in err
    assert radar_store.RUN in err


def test_a_stored_run_must_be_of_the_files_radar(stored, adapt, capsys):
    write_pipeline(stored, "again", run={**STORED, "radar": "KLOT"}, modules=["target_selection"])

    assert adapt("run", "again", "--run-id", radar_store.RUN) == 2

    err = capsys.readouterr().err
    assert "KTST" in err and "KLOT" in err


def test_settings_of_the_stored_run_cannot_be_changed_by_the_file(stored, adapt, capsys):
    write_pipeline(
        stored,
        "again",
        run=STORED,
        modules=["target_selection"],
        params={"tracker": {"max_speed_ms": 30.0}},
    )

    assert adapt("run", "again", "--run-id", radar_store.RUN) == 2

    assert "params.tracker cannot change over a stored run" in capsys.readouterr().err


def test_a_source_folder_that_does_not_exist_is_refused_before_the_run(
    store, live, adapt, home, capsys, tmp_path
):
    absent = tmp_path / "absent"
    live(run={"source_dir": str(absent)})

    assert adapt("run", "case") == 2

    assert str(absent) in capsys.readouterr().err
    assert _no_runs(store)
    assert not (home / ".adapt" / "pipeline.pid").exists()


def test_a_scan_of_another_radar_in_the_source_folder_is_refused_before_the_run(
    store, live, adapt, capsys, tmp_path
):
    (tmp_path / "scans" / "KHTX20240518_120000_V06").write_bytes(b"x" * 6000)
    live()

    assert adapt("run", "case") == 2

    err = capsys.readouterr().err
    assert "KHTX20240518_120000_V06" in err
    assert "KTST" in err
    assert _no_runs(store)


def test_a_source_folder_with_no_scan_in_the_window_is_refused_before_the_run(
    store, live, adapt, capsys
):
    live(run={"start_time": "2024-05-18T14:00:00Z", "end_time": "2024-05-18T15:00:00Z"})

    assert adapt("run", "case") == 2

    err = capsys.readouterr().err
    assert "2024-05-18T14:00:00Z" in err
    assert _no_runs(store)


def test_run_from_a_subfolder_of_a_store_names_the_store_root(store, adapt, monkeypatch, capsys):
    monkeypatch.chdir(store / "collections")

    assert adapt("run", "nexrad") == 1

    err = capsys.readouterr().err
    assert str(store) in err
    assert "adapt init" not in err
