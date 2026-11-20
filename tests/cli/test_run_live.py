# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt run NAME``: the pipeline file's modules over its scans, end to end.

Each run is real — orchestrator, processor, store — over a local folder of
synthetic scans (historical mode). The science upstream of target selection is
one fake module that hands on each scan's cells as the real modules do; target
selection and radar_command are the real ones.
"""

import http.client
import logging
import signal
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime

import pandas as pd
import pytest
from pydantic import BaseModel, ConfigDict, Field
from tests.helpers import radar_store
from tests.helpers.pipeline_files import (
    API,
    API_KEY,
    RADAR_COMMAND,
    TARGET_SELECTION,
    export_api,
    write_pipeline,
)
from tests.helpers.tracking_store import TRACKING_TABLES, tracking_outputs

from adapt import __version__ as adapt_version
from adapt.api import StoreClient
from adapt.contracts import PersistenceMeta, ProductTableWrite
from adapt.execution.module_registry import registry
from adapt.modules.analysis.node import AnalysisModule
from adapt.modules.base import BaseModule
from adapt.modules.cell_volume_stats.node import CellVolumeStatsModule
from adapt.modules.ingest.node import LoadModule
from adapt.modules.tracking.node import TrackingModule
from adapt.modules.tracking.tables import Lifecycle
from adapt.persistence.products import SchemaLedger, TableWriter
from adapt.persistence.store import Store
from adapt.persistence.store_registry import StoreRegistry
from adapt.utils.identity import options_hash

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]

PROBE = "cli_probe"
ECHO = "cli_echo"
CELLS = "cli_cells"
RUN_ID = "2024MAY18-1200-KTST"
RADAR = [CELLS, "target_selection", "radar_command"]
RULE = {"target_selection": TARGET_SELECTION}
RADAR_PARAMS = {**RULE, "radar_command": RADAR_COMMAND}
STORED = {"source": "stored_run", "radar": "KTST", "mode": "historical"}


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """A run configures and shuts down root logging; give pytest its handlers back."""
    root = logging.getLogger()
    saved, level = root.handlers[:], root.level
    yield
    root.handlers[:] = saved
    root.setLevel(level)


@pytest.fixture(autouse=True)
def probe():
    """Module ``cli_probe``: runs each hook in the list, then passes the scan."""
    hooks: list = []

    class _Probe(BaseModule):
        name = PROBE
        inputs = ["scan_id"]
        outputs = ["cli_probe_out"]

        def run(self, context: dict) -> dict:
            for hook in hooks:
                hook()
            return {"cli_probe_out": context["scan_id"]}

    registry.register(_Probe)
    yield hooks
    registry.unregister(PROBE)


class _EchoConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tag: str = Field("untagged", description="What the row records")


@pytest.fixture(autouse=True)
def _echo():
    """Module ``cli_echo``, which owns its options: one row per scan with the tag it was given."""

    class _Echo(BaseModule):
        name = ECHO
        options_class = _EchoConfig
        inputs = [f"{ECHO}_config"]
        outputs = ["cli_echo_rows"]
        persistence = (
            ProductTableWrite(
                key="cli_echo_rows", table="cli_echo", primary_key=("run_id", "scan_id")
            ),
        )

        @classmethod
        def configure(cls, options: _EchoConfig, shared) -> dict:
            return options.model_dump()

        def run(self, context: dict) -> dict:
            return {"cli_echo_rows": pd.DataFrame([context[f"{ECHO}_config"]])}

    registry.register(_Echo)
    yield
    registry.unregister(ECHO)


@pytest.fixture(autouse=True)
def _cells():
    """Module ``cli_cells``: everything up to tracking and cell_volume_stats in one
    step — each scan's two cells (see ``radar_store``), stored as the real modules
    store them. The first scan has no projection, as in a real run."""

    class _Cells(BaseModule):
        name = CELLS
        inputs = ["scan_time", "scan_id"]
        outputs = [
            "grid_ds",
            "cell_stats",
            "cell_adjacency",
            "analysis_ds",
            "cell_identity",
            "cell_events",
            "cell_tracks",
            "cell_volume_stats_rows",
        ]
        persistence = (
            *LoadModule.persistence[:1],
            *AnalysisModule.persistence,
            *TrackingModule.persistence[:1],
            *TRACKING_TABLES,
            *CellVolumeStatsModule.persistence,
        )

        def __init__(self) -> None:
            self._lifecycle = Lifecycle()

        def run(self, context: dict) -> dict:
            index = context["scan_time"].minute // 5  # the scans are 5 minutes apart
            result = radar_store.scan_outputs(index)
            tables, self._lifecycle = tracking_outputs(
                result["tracked_cells"],
                result["cell_events"],
                result["cell_adjacency"],
                scan_id=context["scan_id"],
                scan_time=context["scan_time"],
                lifecycle=self._lifecycle,
            )
            return {key: {**result, **tables}[key] for key in self.outputs}

    registry.register(_Cells)
    yield
    registry.unregister(CELLS)


@pytest.fixture
def ctrl_c():
    """Ctrl-C raises KeyboardInterrupt, as in a foreground shell: a background job
    inherits SIGINT ignored, and Python then installs no handler for it."""
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    yield
    signal.signal(signal.SIGINT, previous)


@pytest.fixture
def case(tmp_path, adapt, monkeypatch, capsys):
    """A store as the current folder, and a folder holding one scan of radar KTST."""
    root = tmp_path / "case"
    adapt("init", str(root))
    monkeypatch.chdir(root)
    scans = tmp_path / "scans"
    scans.mkdir()
    (scans / "KTST20240518_120000_V06").write_bytes(b"x" * 6000)
    capsys.readouterr()
    return root, scans


def _live(case, modules=(PROBE,), **sections):
    """Write ``case.yaml``: the scan folder through ``modules``, plus ``sections``."""
    root, scans = case
    run = {
        "source": "local_directory",
        "radar": "KTST",
        "mode": "historical",
        "start_time": "2024-05-18T12:00:00Z",
        "end_time": "2024-05-18T13:00:00Z",
        "source_dir": str(scans),
    }
    return write_pipeline(root, "case", run=run, modules=list(modules), **sections)


def _more_scans(case, count: int) -> None:
    _, scans = case
    for n in range(1, count + 1):
        # Other bytes than the first: a scan's identity is the hash of its file.
        (scans / f"KTST20240518_12{5 * n:02d}00_V06").write_bytes(bytes([n]) * 6000)


def _run(root, run_id=None):
    with StoreClient(root) as client:
        return client.run(run_id) if run_id else client.latest_run("KTST")


def _ran_with(root, run_id=RUN_ID) -> dict[str, list[str]]:
    """The options (as recorded JSON) each module of the run ran with, by module."""
    recorded: dict[str, list[str]] = {}
    for row in StoreRegistry.get_instance(root).module_provenance(run_id):
        assert row.options_hash == options_hash(row.options_json)
        assert row.version == adapt_version
        recorded.setdefault(row.module, []).append(row.options_json)
    return recorded


def _table(root, name, run_id=RUN_ID) -> pd.DataFrame:
    with StoreClient(root) as client:
        return client.table(name, "KTST", run_id=run_id).sort_values("scan_time")


def test_a_completed_run_exits_zero(case, adapt):
    root, _ = case
    _live(case)

    assert adapt("run", "case") == 0

    assert _run(root).status == "completed"


def test_the_pipeline_file_may_be_named_with_its_extension(case, adapt):
    root, _ = case
    path = _live(case)

    assert adapt("run", "case.yaml") == 0

    run = _run(root)
    with StoreClient(root) as client:
        pipeline = client.run_config(run.run_id)["pipeline"]
    assert run.status == "completed"
    assert (pipeline["name"], pipeline["file"]) == ("case", str(path))


def test_the_run_records_its_pipeline_file(case, adapt):
    root, _ = case
    path = _live(case, modules=[PROBE, ECHO], params={ECHO: {"tag": "recorded"}})

    adapt("run", "case")

    run_id = _run(root).run_id
    with StoreClient(root) as client:
        pipeline = client.run_config(run_id)["pipeline"]
    assert (pipeline["name"], pipeline["file"]) == ("case", str(path))
    assert pipeline["content"]["modules"] == [PROBE, ECHO]
    assert pipeline["content"]["params"] == {ECHO: {"tag": "recorded"}}
    assert f"config {path}" in (root / "logs" / "pipeline_KTST.log").read_text()


def test_verbose_runs_with_debug_logging(case, adapt):
    root, _ = case
    _live(case)

    adapt("run", "case", "-v")

    with StoreClient(root) as client:
        assert client.run_config(_run(root).run_id)["logging"]["level"] == "DEBUG"


def test_verbose_shows_debug_output_on_the_console(case, adapt, capsys):
    _live(case)

    assert adapt("run", "case", "-v") == 0

    assert "Pipeline running in HISTORICAL mode" in capsys.readouterr().err


def test_a_module_that_owns_its_options_gets_the_files(case, adapt):
    root, _ = case
    _live(case, modules=[PROBE, ECHO], params={ECHO: {"tag": "live"}})

    assert adapt("run", "case", "--run-id", RUN_ID) == 0

    assert _table(root, "cli_echo")["tag"].tolist() == ["live"]


def test_target_selection_and_radar_command_act_on_every_scan_in_order(case, adapt, capsys):
    """Three scans; the first has no projection. uid-a (50 dBZ) is the one cell
    with a 45 dBZ core: selected at the first scan and kept."""
    root, _ = case
    _more_scans(case, 2)
    _live(case, modules=RADAR, params=RADAR_PARAMS)

    assert adapt("run", "case", "--run-id", RUN_ID) == 0

    selection = _table(root, "target_selection")
    assert selection["cell_uid"].tolist() == [radar_store.UID_A] * 3
    assert selection["reason"].tolist() == ["NEW_TARGET", "CONTINUATION", "CONTINUATION"]
    commands = _table(root, "radar_commands")
    # The first scan has no projected footprint to aim at; the next two do.
    assert commands["outcome"].tolist() == ["TARGET_UNAVAILABLE", "PUBLISHED", "PUBLISHED"]
    assert commands["scan_id"].tolist() == selection["scan_id"].tolist()
    assert commands["selection_config_hash"].tolist() == selection["config_hash"].tolist()
    assert commands["plan_id"].iloc[1] != commands["plan_id"].iloc[2]
    assert '"scan_pattern": "ppi_sector"' in commands["command_json"].iloc[2]
    # What the radar was asked to do is on the console, one line per scan.
    console = capsys.readouterr().err
    assert console.count("radar_command: command") == 2
    assert f"written for cell {radar_store.UID_A}" in console


def test_a_radar_api_that_cannot_be_reached_is_recorded_and_the_run_goes_on(
    case, adapt, monkeypatch, capsys
):
    root, _ = case
    _more_scans(case, 1)
    export_api(monkeypatch)

    def refused(connection):  # no socket is opened: the test reaches no network or proxy
        raise ConnectionRefusedError(61, "Connection refused")

    monkeypatch.setattr(http.client.HTTPSConnection, "connect", refused)
    any_age = {**API, "max_scan_age_seconds": 1.0e12}  # the case's scans are years old
    params = {**RADAR_PARAMS, "radar_command": {**RADAR_COMMAND, "api": any_age}}
    _live(case, modules=RADAR, params=params)

    assert adapt("run", "case", "--run-id", RUN_ID) == 0

    commands = _table(root, "radar_commands")
    assert commands["outcome"].tolist() == ["TARGET_UNAVAILABLE", "SEND_FAILED"]
    assert "unreachable" in commands["detail"].iloc[1]
    assert "✗– radar_command: command" in capsys.readouterr().err
    assert API_KEY not in commands.to_json()  # the key is never stored


def test_a_run_over_old_scans_sends_none_of_them(case, adapt, monkeypatch, capsys):
    """Historical scans with ``api:`` set: every command is held back, none goes out."""
    root, _ = case
    _more_scans(case, 2)
    export_api(monkeypatch)
    attempts = []

    def attempted(connection):  # records an attempt, then fails as a closed port does
        attempts.append(connection)
        raise ConnectionRefusedError(61, "Connection refused")

    monkeypatch.setattr(http.client.HTTPSConnection, "connect", attempted)
    params = {**RADAR_PARAMS, "radar_command": {**RADAR_COMMAND, "api": API}}
    _live(case, modules=RADAR, params=params)

    assert adapt("run", "case", "--run-id", RUN_ID) == 0

    commands = _table(root, "radar_commands")
    assert commands["outcome"].tolist() == ["TARGET_UNAVAILABLE", "STALE", "STALE"]
    assert attempts == []
    assert capsys.readouterr().err.count("held back for cell") == 2


@pytest.mark.usefixtures("ctrl_c")
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM], ids=["ctrl-c", "sigterm"])
def test_a_stop_signal_cancels_the_run(case, adapt, probe, capsys, signum):
    root, _ = case
    _live(case)
    probe.append(lambda: signal.raise_signal(signum))

    assert adapt("run", "case", "--run-id", RUN_ID) == 1

    assert _run(root, RUN_ID).status == "cancelled"


def test_a_run_with_failed_scans_exits_1_and_does_not_claim_success(case, adapt, probe, capsys):
    root, _ = case
    _live(case)
    probe.append(lambda: 1 / 0)  # every scan fails inside the module

    assert adapt("run", "case", "--run-id", RUN_ID) == 1

    out = capsys.readouterr()
    assert "SUCCESS" not in out.out + out.err
    assert "Scans that failed: 1" in out.out
    assert _run(root, RUN_ID).status == "completed"


def test_failed_scans_are_told_with_metrics_switched_off(case, adapt, probe, capsys):
    """Whether a scan failed is a fact of the run, not a metric."""
    _live(case, params={"logging": {"metrics": False}})
    probe.append(lambda: 1 / 0)  # every scan fails inside the module

    assert adapt("run", "case", "--run-id", RUN_ID) == 1

    out = capsys.readouterr()
    assert "SUCCESS" not in out.out + out.err
    assert "Scans that failed: 1" in out.out


def test_a_scan_whose_rows_the_store_refuses_fails_the_run(case, adapt, capsys):
    """The module ran, but its table cannot be written: in this collection it
    belongs to another module."""
    root, _ = case
    store = Store.open(root)
    collection = store.collection("KTST")
    taken = ProductTableWrite(key="rows", table="cli_echo", primary_key=("run_id", "scan_id"))
    TableWriter(
        SchemaLedger(collection.products_path, collection.catalog, ()), taken, owner_module="other"
    ).write(
        pd.DataFrame({"tag": ["theirs"]}),
        PersistenceMeta(
            scan_time=datetime(2024, 5, 18, 11, 0, tzinfo=UTC),
            scan_id="an-older-scan",
            run_id="2024MAY18-1100-KTST",
            source_file="KTST20240518_110000_V06",
            collection_id="KTST",
        ),
    )
    store.close()
    _live(case, modules=[ECHO])

    assert adapt("run", "case", "--run-id", RUN_ID) == 1

    out = capsys.readouterr()
    assert "SUCCESS" not in out.out + out.err
    assert "Scans that failed: 1" in out.out
    with StoreClient(root) as client:
        assert client.scans("KTST", run_id=RUN_ID) == []  # no scan of the run is complete


def test_continuing_a_run_needs_the_files_radar(case, adapt, capsys):
    root, _ = case
    _live(case)
    adapt("run", "case", "--run-id", RUN_ID)
    run = {"source": "aws_nexrad", "radar": "KOTH", "mode": "realtime"}
    write_pipeline(root, "other", run=run, modules=[PROBE])
    capsys.readouterr()

    assert adapt("run", "other", "--run-id", RUN_ID) == 2

    err = capsys.readouterr().err
    assert "KOTH" in err
    assert "KTST" in err


def test_a_pid_file_left_by_a_pipeline_that_has_exited_is_ignored(case, adapt, home):
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    exited.wait()
    (home / ".adapt").mkdir()
    (home / ".adapt" / "pipeline.pid").write_text(str(exited.pid))
    _live(case)

    assert adapt("run", "case") == 0


def test_the_pid_file_is_removed_after_the_run(case, adapt, home):
    _live(case)

    assert adapt("run", "case") == 0

    assert not (home / ".adapt" / "pipeline.pid").exists()


# ---------------------------------------------------------------------------
# Over a stored run (run.source: stored_run)
# ---------------------------------------------------------------------------


@pytest.fixture
def stored(case, adapt, capsys):
    """Run ``RUN_ID``: three scans through ``cli_cells`` only, completed."""
    root, _ = case
    _more_scans(case, 2)
    _live(case, modules=[CELLS])
    assert adapt("run", "case", "--run-id", RUN_ID) == 0
    capsys.readouterr()
    return root


def test_modules_added_later_run_over_the_stored_run_reading_the_store(stored, adapt, capsys):
    write_pipeline(stored, "later", run=STORED, modules=RADAR[1:], params=RADAR_PARAMS)

    assert adapt("run", "later", "--run-id", RUN_ID) == 0

    assert _table(stored, "target_selection")["cell_uid"].tolist() == [radar_store.UID_A] * 3
    outcomes = _table(stored, "radar_commands")["outcome"].tolist()
    assert outcomes == ["TARGET_UNAVAILABLE", "PUBLISHED", "PUBLISHED"]
    assert _run(stored, RUN_ID).status == "completed"  # the stored run ends as it was
    assert f"over stored run {RUN_ID}" in capsys.readouterr().out


def test_the_stored_runs_own_tables_are_left_as_they_were(stored, adapt):
    before = _table(stored, "cells_by_scan").to_json()
    write_pipeline(stored, "later", run=STORED, modules=["target_selection"], params=RULE)

    assert adapt("run", "later", "--run-id", RUN_ID) == 0

    assert _table(stored, "cells_by_scan").to_json() == before


def test_a_rule_may_gate_on_a_volume_statistic(stored, adapt):
    """Volume statistics are columns of the cells target selection reads: the
    cell's top is 9500, 9600 and 9700 m over the three scans."""
    tall = {
        **TARGET_SELECTION,
        "entry": {"gates": [{"field": "cell_top_m", "op": "ge", "value": 9650.0}]},
    }
    write_pipeline(
        stored, "tall", run=STORED, modules=["target_selection"], params={"target_selection": tall}
    )

    assert adapt("run", "tall", "--run-id", RUN_ID) == 0

    chosen = _table(stored, "target_selection")["cell_uid"]
    assert chosen.notna().tolist() == [False, False, True]
    assert chosen.iloc[-1] == radar_store.UID_A


def test_another_rule_over_the_same_run_is_kept_beside_the_first(stored, adapt):
    """Decisions under different rules are different facts: neither replaces the other."""
    write_pipeline(stored, "later", run=STORED, modules=["target_selection"], params=RULE)
    adapt("run", "later", "--run-id", RUN_ID)
    stricter = {
        **TARGET_SELECTION,
        "entry": {"gates": [{"field": "radar_reflectivity_max", "op": "ge", "value": 55.0}]},
    }
    write_pipeline(
        stored,
        "stricter",
        run=STORED,
        modules=["target_selection"],
        params={"target_selection": stricter},
    )

    assert adapt("run", "stricter", "--run-id", RUN_ID) == 0

    selection = _table(stored, "target_selection")
    assert len(selection) == 6
    by_rule = selection.groupby("config_hash")["cell_uid"].apply(lambda uids: uids.notna().sum())
    assert sorted(by_rule) == [0, 3]  # no cell reaches 55 dBZ; uid-a is chosen at 45


def test_running_the_same_modules_over_it_again_changes_nothing(stored, adapt):
    write_pipeline(stored, "later", run=STORED, modules=RADAR[1:], params=RADAR_PARAMS)
    adapt("run", "later", "--run-id", RUN_ID)
    first = (
        _table(stored, "target_selection").to_json(),
        _table(stored, "radar_commands").to_json(),
    )

    assert adapt("run", "later", "--run-id", RUN_ID) == 0

    again = (
        _table(stored, "target_selection").to_json(),
        _table(stored, "radar_commands").to_json(),
    )
    assert again == first


def test_radar_command_never_sends_over_a_stored_run(stored, adapt, monkeypatch):
    for variable in ("ADAPT_TEST_URL", "ADAPT_TEST_KEY", "ADAPT_TEST_CA"):
        monkeypatch.delenv(variable, raising=False)  # none is needed: nothing is sent
    params = {**RADAR_PARAMS, "radar_command": {**RADAR_COMMAND, "api": API}}
    write_pipeline(stored, "later", run=STORED, modules=RADAR[1:], params=params)

    assert adapt("run", "later", "--run-id", RUN_ID) == 0

    outcomes = _table(stored, "radar_commands")["outcome"].tolist()
    assert outcomes == ["TARGET_UNAVAILABLE", "PUBLISHED", "PUBLISHED"]


@pytest.fixture
def tagged(case, adapt, capsys):
    """Run ``RUN_ID``: one scan through ``cli_echo`` with the tag "first", completed."""
    root, _ = case
    _live(case, modules=[PROBE, ECHO], params={ECHO: {"tag": "first"}})
    assert adapt("run", "case", "--run-id", RUN_ID) == 0
    capsys.readouterr()
    return root


def test_a_module_run_again_with_other_options_is_refused_and_its_rows_kept(tagged, adapt, capsys):
    """``cli_echo``'s rows are keyed by scan alone: the pass would write over them."""
    write_pipeline(tagged, "later", run=STORED, modules=[ECHO], params={ECHO: {"tag": "second"}})

    assert adapt("run", "later", "--run-id", RUN_ID) == 2

    err = capsys.readouterr().err
    assert f"run {RUN_ID} ran {ECHO} with other options" in err
    assert f'  • params.{ECHO}.tag — recorded: "first"; this file: "second"' in err
    assert "start a new run" in err
    assert _table(tagged, "cli_echo")["tag"].tolist() == ["first"]


def test_a_module_run_again_with_the_recorded_options_writes_the_same_rows(tagged, adapt):
    before = _table(tagged, "cli_echo").to_json()
    write_pipeline(tagged, "later", run=STORED, modules=[ECHO], params={ECHO: {"tag": "first"}})

    assert adapt("run", "later", "--run-id", RUN_ID) == 0

    assert _table(tagged, "cli_echo").to_json() == before


# ---------------------------------------------------------------------------
# What each module ran with is kept with the run
# ---------------------------------------------------------------------------


def test_a_run_keeps_the_options_each_of_its_modules_ran_with(tagged):
    assert _ran_with(tagged) == {PROBE: ["null"], ECHO: ['{"tag":"first"}']}


def test_a_pass_keeps_the_options_of_the_modules_it_ran(stored, adapt):
    write_pipeline(stored, "later", run=STORED, modules=[ECHO], params={ECHO: {"tag": "late"}})

    assert adapt("run", "later", "--run-id", RUN_ID) == 0

    assert _ran_with(stored) == {CELLS: ["null"], ECHO: ['{"tag":"late"}']}


def test_a_module_first_run_by_a_pass_cannot_be_run_again_with_other_options(stored, adapt, capsys):
    """The run itself never ran ``cli_echo``; the first pass did, and its rows
    are keyed by scan alone, so a second pass would write over some of them."""
    write_pipeline(stored, "later", run=STORED, modules=[ECHO], params={ECHO: {"tag": "late"}})
    assert adapt("run", "later", "--run-id", RUN_ID) == 0
    write_pipeline(stored, "again", run=STORED, modules=[ECHO], params={ECHO: {"tag": "other"}})

    assert adapt("run", "again", "--run-id", RUN_ID) == 2

    err = capsys.readouterr().err
    assert f"run {RUN_ID} ran {ECHO} with other options" in err
    assert f'  • params.{ECHO}.tag — recorded: "late"; this file: "other"' in err
    assert _table(stored, "cli_echo")["tag"].tolist() == ["late"] * 3
    assert _ran_with(stored)[ECHO] == ['{"tag":"late"}']


def test_a_module_first_run_by_a_pass_may_be_run_again_with_the_same_options(stored, adapt):
    write_pipeline(stored, "later", run=STORED, modules=[ECHO], params={ECHO: {"tag": "late"}})
    assert adapt("run", "later", "--run-id", RUN_ID) == 0

    assert adapt("run", "later", "--run-id", RUN_ID) == 0

    assert _ran_with(stored)[ECHO] == ['{"tag":"late"}']


def test_every_rule_a_run_was_decided_with_is_kept_under_the_hash_in_its_rows(stored, adapt):
    """``target_selection`` keys its rows by the hash of its rules: the run keeps
    each set of rules under that same hash."""
    write_pipeline(stored, "later", run=STORED, modules=["target_selection"], params=RULE)
    adapt("run", "later", "--run-id", RUN_ID)
    stricter = {
        **TARGET_SELECTION,
        "entry": {"gates": [{"field": "radar_reflectivity_max", "op": "ge", "value": 55.0}]},
    }
    write_pipeline(
        stored,
        "stricter",
        run=STORED,
        modules=["target_selection"],
        params={"target_selection": stricter},
    )
    assert adapt("run", "stricter", "--run-id", RUN_ID) == 0

    kept = {options_hash(text) for text in _ran_with(stored)["target_selection"]}

    assert kept == set(_table(stored, "target_selection")["config_hash"])
    assert len(kept) == 2


def test_radar_command_rows_carry_the_hash_of_the_options_kept_with_the_run(stored, adapt):
    write_pipeline(stored, "later", run=STORED, modules=RADAR[1:], params=RADAR_PARAMS)

    assert adapt("run", "later", "--run-id", RUN_ID) == 0

    (kept,) = _ran_with(stored)["radar_command"]
    assert set(_table(stored, "radar_commands")["config_hash"]) == {options_hash(kept)}


def test_over_a_stored_run_radar_command_rows_carry_the_hash_of_its_options_without_api(
    stored, adapt, monkeypatch
):
    """A pass never sends, so ``radar_command`` drops ``api`` before it plans:
    its rows carry the hash of the options without it, the run keeps them whole."""
    for variable in ("ADAPT_TEST_URL", "ADAPT_TEST_KEY", "ADAPT_TEST_CA"):
        monkeypatch.delenv(variable, raising=False)
    params = {**RADAR_PARAMS, "radar_command": {**RADAR_COMMAND, "api": API}}
    write_pipeline(stored, "later", run=STORED, modules=RADAR[1:], params=params)
    assert adapt("run", "later", "--run-id", RUN_ID) == 0

    (kept,) = _ran_with(stored)["radar_command"]

    assert '"api":{' in kept
    assert set(_table(stored, "radar_commands")["config_hash"]) != {options_hash(kept)}


def test_a_run_that_kept_no_options_of_its_modules_cannot_be_passed_over(stored, adapt, capsys):
    """Runs recorded before Adapt kept each module's options have no such rows."""
    registry_db = sqlite3.connect(stored / "registry.db")
    try:
        registry_db.execute("DELETE FROM run_module_provenance")
        registry_db.commit()
    finally:
        registry_db.close()
    write_pipeline(stored, "later", run=STORED, modules=[ECHO], params={ECHO: {"tag": "late"}})

    assert adapt("run", "later", "--run-id", RUN_ID) == 2

    err = capsys.readouterr().err
    assert RUN_ID in err
    assert "recorded before" in err
    assert "start a new run" in err
