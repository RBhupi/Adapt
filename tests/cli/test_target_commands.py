# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""`adapt tse run` and `adapt radar-command run` over real synthetic stores."""

import json
import re
import time

import pytest
from tests.api.synthetic_store import build_synthetic_store
from tests.consumers.radar_command import synthetic_store as rc_store
from tests.consumers.target_selection.factories import make_scan_decision

from adapt import cli
from adapt.api import StoreError
from adapt.consumers.target_selection.selection import (
    SelectionReason,
    TargetSelection,
)
from adapt.consumers.target_selection.writer import write_current_selection
from adapt.persistence.store import Store, init_store
from adapt.persistence.store_registry import StoreRegistry

pytestmark = pytest.mark.unit


@pytest.fixture
def consumers_yaml(tmp_path):
    """The one YAML file both commands read, each its own section."""
    path = tmp_path / "consumers.yaml"
    path.write_text(
        f"""\
target_selection:
  candidate:
    gates: [{{field: radar_reflectivity_max, op: ge, value: 45.0}}]
    min_age_seconds: 0.0
  priority: {{weights: {{field: 1.0, area: 0.05, growth_rate: 2.0}}}}
  site_preference: {{projection_steps: 5, sites: []}}
  selection: {{switch_margin: 5.0, max_observation_seconds: 1800.0}}
  snapshot: {{growth_window_scans: 2}}
radar_command:
  radar: {{scan_pattern: ppi_sector, user_id: operator}}
  extent: {{horizontal_buffer_km: 1.0}}
  output: {{directory: '{tmp_path / "out"}'}}
""",
        encoding="utf-8",
    )
    return path


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _tse_argv(root, config, selection, *extra):
    return [
        "adapt",
        "tse",
        "run",
        "--repo",
        str(root),
        "--collection",
        "KTST",
        "--config",
        str(config),
        "--selection-file",
        str(selection),
        *extra,
    ]


def test_tse_run_writes_the_selection_for_the_runs_last_complete_scan(
    tmp_path, consumers_yaml, monkeypatch
):
    built = build_synthetic_store(tmp_path / "built")
    selection = tmp_path / "current_selection.json"
    monkeypatch.setattr(
        "sys.argv", _tse_argv(built.root, consumers_yaml, selection, "--run-id", "run-1")
    )

    cli.main()

    # run-1 completes 12:00 and 12:05 (12:10 is pending). Score at 12:05 =
    # 1.0 * 46 dBZ + 0.05 * 20 km2 + 2.0 * (20 - 10) km2 / 5 min = 51.0
    written = _read(selection)
    assert written == {
        "schema": 1,
        "collection": "KTST",
        "run_id": "run-1",
        "scan_id": built.scan_ids[1],
        "scan_time": "2026-06-01T12:05:00Z",
        "config_hash": written["config_hash"],
        "cell_uid": "uid-1",
        "reason": "CONTINUATION",
        "score": pytest.approx(51.0),
        "stop_reason": None,
        "previous_cell_uid": "uid-1",
    }


def test_tse_run_without_run_id_selects_on_the_latest_run(tmp_path, consumers_yaml, monkeypatch):
    # run-2 started after run-1; its one complete scan reuses the 12:00 volume.
    built = build_synthetic_store(tmp_path / "built")
    selection = tmp_path / "current_selection.json"
    monkeypatch.setattr("sys.argv", _tse_argv(built.root, consumers_yaml, selection))

    cli.main()

    written = _read(selection)
    assert (written["run_id"], written["scan_id"], written["scan_time"], written["cell_uid"]) == (
        "run-2",
        built.scan_ids[0],
        "2026-06-01T12:00:00Z",
        "uid-r2",
    )


def test_tse_run_on_a_collection_without_runs_fails_naming_it(
    tmp_path, consumers_yaml, monkeypatch
):
    root = init_store(tmp_path / "store")
    selection = tmp_path / "current_selection.json"
    monkeypatch.setattr("sys.argv", _tse_argv(root, consumers_yaml, selection))

    with pytest.raises(ValueError, match="No runs of collection 'KTST'"):
        cli.main()
    assert not selection.exists()


def test_tse_run_with_an_unknown_run_id_fails_naming_it(tmp_path, consumers_yaml, monkeypatch):
    built = build_synthetic_store(tmp_path / "built")
    selection = tmp_path / "current_selection.json"
    selection.write_text("previous selection\n", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv", _tse_argv(built.root, consumers_yaml, selection, "--run-id", "run-1-TYPO")
    )

    with pytest.raises(StoreError, match="run-1-TYPO"):
        cli.main()
    assert selection.read_text(encoding="utf-8") == "previous selection\n"


def test_tse_run_with_a_run_of_another_collection_fails_naming_both(
    tmp_path, consumers_yaml, monkeypatch
):
    built = build_synthetic_store(tmp_path / "built")  # run-1 belongs to KTST
    store = Store.open(built.root)
    store.collection("KOTH")  # another radar's collection in the same store
    store.close()
    selection = tmp_path / "current_selection.json"
    argv = _tse_argv(built.root, consumers_yaml, selection, "--run-id", "run-1")
    argv[argv.index("KTST")] = "KOTH"
    monkeypatch.setattr("sys.argv", argv)

    with pytest.raises(ValueError, match=r"run-1.*KTST.*KOTH"):
        cli.main()
    assert not selection.exists()


def test_tse_run_reports_scans_processed_in_one_line(tmp_path, consumers_yaml, monkeypatch, capsys):
    built = build_synthetic_store(tmp_path / "built")
    selection = tmp_path / "current_selection.json"
    monkeypatch.setattr(
        "sys.argv", _tse_argv(built.root, consumers_yaml, selection, "--run-id", "run-1")
    )

    cli.main()

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert "2 scans" in lines[0] and "run-1" in lines[0] and str(selection) in lines[0]


def test_tse_run_follow_polls_the_running_run_until_it_ends(tmp_path, consumers_yaml, monkeypatch):
    """run-2 is still running; it finishes while the command waits."""
    built = build_synthetic_store(tmp_path / "built")
    waits = []

    def _run_finishes(seconds):
        waits.append(seconds)
        StoreRegistry.get_instance(built.root).finalize_run("run-2", "completed", scans_processed=1)

    monkeypatch.setattr(time, "sleep", _run_finishes)
    selection = tmp_path / "current_selection.json"
    monkeypatch.setattr(
        "sys.argv",
        _tse_argv(
            built.root,
            consumers_yaml,
            selection,
            "--run-id",
            "run-2",
            "--follow",
            "--poll-seconds",
            "0.5",
        ),
    )

    cli.main()

    assert waits == [0.5]


def _select_cell(tmp_path, built, cell_uid=rc_store.UID_A):
    """The selection the engine writes for ``cell_uid`` on the 12:05:37 scan."""
    path = tmp_path / "current_selection.json"
    scan_time = rc_store.SCAN_TIMES[1]
    selection = TargetSelection(
        cell_uid=cell_uid,
        reason=SelectionReason.NEW_TARGET,
        score=80.0,
        selection_time=scan_time,
        trajectory=(),
        observation_window=(scan_time, scan_time),
    )
    write_current_selection(
        path,
        make_scan_decision(
            rc_store.COLLECTION, rc_store.RUN, built.scan_ids[1], scan_time, selection
        ),
    )
    return path


def _radar_command_argv(root, config, selection, *extra):
    return [
        "adapt",
        "radar-command",
        "run",
        "--repo",
        str(root),
        "--config",
        str(config),
        "--selection-file",
        str(selection),
        *extra,
    ]


def test_radar_command_run_publishes_the_current_targets_file(
    tmp_path, consumers_yaml, monkeypatch
):
    built = rc_store.build_store(tmp_path / "built", volume_stats=True)
    monkeypatch.setattr(
        "sys.argv",
        _radar_command_argv(built.root, consumers_yaml, _select_cell(tmp_path, built)),
    )

    cli.main()

    command = _read(tmp_path / "out" / "current_targets.json")
    assert (command["scan_pattern"], command["user_id"]) == ("ppi_sector", "operator")
    assert set(command) >= {"lat", "lon", "alt", "lat_extent", "lon_extent", "alt_extent"}


def test_radar_command_run_reports_the_published_plan_in_one_line(
    tmp_path, consumers_yaml, monkeypatch, capsys
):
    built = rc_store.build_store(tmp_path / "built", volume_stats=True)
    monkeypatch.setattr(
        "sys.argv",
        _radar_command_argv(built.root, consumers_yaml, _select_cell(tmp_path, built)),
    )

    cli.main()

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert re.search(r"\bPUBLISHED plan [0-9a-f]{16}\b", lines[0])
    assert built.scan_ids[1] in lines[0] and "uid-a" in lines[0]


def test_radar_command_run_reports_an_unavailable_target_and_exits_non_zero(
    tmp_path, consumers_yaml, monkeypatch, capsys
):
    built = rc_store.build_store(tmp_path / "built", volume_stats=True)
    selection = _select_cell(tmp_path, built, cell_uid="uid-zz")  # not in the scan
    monkeypatch.setattr("sys.argv", _radar_command_argv(built.root, consumers_yaml, selection))

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert "TARGET_UNAVAILABLE" in lines[0] and "uid-zz" in lines[0]


@pytest.mark.parametrize("argv", [_tse_argv, _radar_command_argv])
def test_missing_config_file_fails_naming_it(tmp_path, monkeypatch, argv):
    root = init_store(tmp_path / "store")
    selection = tmp_path / "current_selection.json"
    monkeypatch.setattr("sys.argv", argv(root, tmp_path / "missing.yaml", selection))

    with pytest.raises(FileNotFoundError, match="missing.yaml"):
        cli.main()
    assert not selection.exists()
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("argv", [_tse_argv, _radar_command_argv])
@pytest.mark.parametrize("seconds", ["0", "-1", "nan", "inf"])
def test_poll_seconds_must_be_positive_and_finite(
    tmp_path, consumers_yaml, monkeypatch, capsys, argv, seconds
):
    root = init_store(tmp_path / "store")
    selection = tmp_path / "current_selection.json"
    monkeypatch.setattr(
        "sys.argv", argv(root, consumers_yaml, selection, "--poll-seconds", seconds)
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    assert "--poll-seconds" in capsys.readouterr().err


def test_help_lists_both_commands(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["adapt", "--help"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 0
    commands = {line.split()[0] for line in capsys.readouterr().out.splitlines() if line.strip()}
    assert {"tse", "radar-command"} <= commands


class _Stop(Exception):
    """Ends the otherwise endless follow loop."""


def test_radar_command_run_follow_keeps_polling_the_selection_file(
    tmp_path, consumers_yaml, monkeypatch
):
    built = rc_store.build_store(tmp_path / "built", volume_stats=True)
    waits = []

    def _stop(seconds):
        waits.append(seconds)
        raise _Stop

    monkeypatch.setattr(time, "sleep", _stop)
    selection = _select_cell(tmp_path, built)
    monkeypatch.setattr(
        "sys.argv",
        _radar_command_argv(
            built.root, consumers_yaml, selection, "--follow", "--poll-seconds", "0.5"
        ),
    )

    with pytest.raises(_Stop):
        cli.main()
    assert waits == [0.5]


class _Accepted:
    status = 200

    def read(self):
        return b"accepted"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_tse_then_radar_command_sends_the_selected_cell_to_the_api(
    tmp_path, consumers_yaml, monkeypatch, capsys
):
    """End to end: the store -> tse run -> selection file -> radar-command run -> POST."""
    built = rc_store.build_store(tmp_path / "built", volume_stats=True)
    selection = tmp_path / "current_selection.json"
    with consumers_yaml.open("a", encoding="utf-8") as f:
        f.write(
            "  api: {base_url: 'https://radar.example.test', "
            "api_key_env: ADAPT_TEST_KEY, timeout_seconds: 5}\n"
        )
    monkeypatch.setenv("ADAPT_TEST_KEY", "k-secret")
    posted = []

    def urlopen(request, timeout):
        posted.append((request.full_url, request.get_header("X-api-key"), json.loads(request.data)))
        return _Accepted()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    monkeypatch.setattr(
        "sys.argv", _tse_argv(built.root, consumers_yaml, selection, "--run-id", rc_store.RUN)
    )
    cli.main()
    chosen = _read(selection)["cell_uid"]
    monkeypatch.setattr("sys.argv", _radar_command_argv(built.root, consumers_yaml, selection))
    cli.main()

    ((url, key, payload),) = posted
    assert url == "https://radar.example.test/publish/command"
    assert key == "k-secret"
    assert chosen is not None and payload["scan_pattern"] == "ppi_sector"
    assert "SENT" in capsys.readouterr().out
