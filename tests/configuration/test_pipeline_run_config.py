# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``run_config``: a pipeline file becomes the run's InternalConfig.

``run:`` maps onto the source and downloader settings, ``modules`` is the run's
module list, ``params:`` sections are laid over their defaults and module-owned
params route to ``module_params``, defaults filled in. The file's
name, path and content are recorded with the run. A ``--run-id`` naming a stored
run reloads that run's recorded config; anything else is a new run. The file's
folder is the store root.
"""

import json
import shutil
import sqlite3
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ConfigDict

from adapt import __version__
from adapt.configuration.pipeline_file import PipelineFile
from adapt.configuration.pipeline_run import run_config
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from adapt.persistence.errors import StoreError
from adapt.persistence.store import init_store
from adapt.persistence.store_registry import RunStart, StoreRegistry
from adapt.runtime.provenance import module_provenance
from adapt.utils.identity import options_hash
from tests.helpers.pipeline_files import API, export_api, with_radar_command
from tests.helpers.run_configs import record_run

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _flash_sorted_file(tmp_path):
    """``tmp_path`` passes as an xlma_stat input folder: it holds one NetCDF file."""
    (tmp_path / "flashes.nc").write_bytes(b"")


_CORE = ("ingest", "detection", "projection", "analysis", "tracking")
_MODULES = (*_CORE, "cell_volume_stats")
_RUN_ID = "2026MAR23-0206-KBOX"


def _pipeline(**overrides) -> PipelineFile:
    data = {
        "run": {"source": "aws_nexrad", "radar": "KBOX", "mode": "realtime"},
        "modules": _MODULES,
        "params": {},
    }
    data.update(overrides)
    data["params"] = with_radar_command(data["modules"], data["params"])
    return PipelineFile.model_validate(data)


@pytest.fixture
def store_root(tmp_path):
    return init_store(tmp_path / "store")


@pytest.fixture
def path(store_root):
    """Where the pipeline file lives: in the store root, named after the pipeline."""
    return store_root / "case.yaml"


class TestRunSection:
    def test_the_files_folder_is_the_base_dir(self, path, store_root):
        assert run_config(_pipeline(), path).base_dir == str(store_root)

    def test_realtime_run_maps_source_and_radar(self, path):
        config = run_config(_pipeline(), path)

        assert config.mode == "realtime"
        assert config.source == "aws_nexrad"
        assert config.downloader.radar == "KBOX"
        assert config.downloader.mode == "realtime"

    def test_historical_run_maps_the_time_range_as_utc_strings(self, path):
        run = {
            "source": "aws_nexrad",
            "radar": "KBOX",
            "mode": "historical",
            "start_time": datetime(2025, 6, 17, 18, 0, tzinfo=UTC),
            "end_time": "2025-06-17T22:30:00+02:00",
        }

        config = run_config(_pipeline(run=run), path)

        assert config.mode == "historical"
        assert config.downloader.start_time == "2025-06-17T18:00:00Z"
        assert config.downloader.end_time == "2025-06-17T20:30:00Z"

    def test_historical_run_without_a_time_range_is_rejected(self, path):
        run = {"source": "aws_nexrad", "radar": "KBOX", "mode": "historical"}

        with pytest.raises(ConfigError, match="start_time"):
            run_config(_pipeline(run=run), path)

    def test_local_directory_source_maps_its_folder(self, path, tmp_path):
        run = {
            "source": "local_directory",
            "radar": "KBOX",
            "mode": "historical",
            "start_time": "2025-06-17T18:00:00Z",
            "end_time": "2025-06-17T19:00:00Z",
            "source_dir": str(tmp_path),
        }

        config = run_config(_pipeline(run=run), path)

        assert config.source == "local_directory"
        assert config.source_dir == str(tmp_path)


class TestRecordedConfig:
    @pytest.mark.parametrize("key", ["visualization", "output", "processor", "output_dirs"])
    def test_a_section_nothing_reads_is_not_recorded(self, path, key):
        recorded = json.loads(run_config(_pipeline(), path).model_dump_json())

        assert key not in recorded


class TestModules:
    def test_the_runs_modules_are_the_files_in_file_order(self, path):
        pipeline = _pipeline(modules=("tracking", *_MODULES[:-2], "cell_volume_stats"))

        config = run_config(pipeline, path)

        assert config.modules == ["tracking", *_MODULES[:-2], "cell_volume_stats"]


class TestParams:
    def test_section_options_override_their_defaults_only(self, path):
        params = {"logging": {"level": "DEBUG"}}

        config = run_config(_pipeline(params=params), path)

        assert config.logging.level == "DEBUG"
        assert config.logging.console_level == "WARNING"
        assert config.global_.max_scan_gap_minutes == 10.0

    def test_global_section_is_merged(self, path):
        config = run_config(_pipeline(params={"global": {"z_level": 3000}}), path)

        assert config.global_.z_level == 3000.0

    def test_downloader_section_merges_with_the_run_section(self, path):
        params = {"downloader": {"poll_interval_sec": 60}}

        config = run_config(_pipeline(params=params), path)

        assert config.downloader.poll_interval_sec == 60
        assert config.downloader.radar == "KBOX"

    def test_module_owned_params_route_to_module_params(self, path, tmp_path):
        params = {
            "cell_volume_stats": {"gap_tolerance_m": 250.0},
            "xlma_stat": {"input_dir": str(tmp_path)},
        }

        config = run_config(_pipeline(modules=(*_MODULES, "xlma_stat"), params=params), path)

        assert config.module_params["cell_volume_stats"]["gap_tolerance_m"] == 250.0
        assert config.module_params["xlma_stat"]["input_dir"] == str(tmp_path)

    def test_module_params_record_every_listed_modules_defaults(self, path, tmp_path):
        pipeline = _pipeline(
            modules=(*_MODULES, "xlma_stat"),
            params={
                "cell_volume_stats": {"gap_tolerance_m": 250.0},
                "xlma_stat": {"input_dir": str(tmp_path)},
            },
        )

        config = run_config(pipeline, path)

        assert config.module_params["cell_volume_stats"] == {
            "zdr_var": "differential_reflectivity",
            "kdp_var": "specific_differential_phase",
            "rhohv_var": "cross_correlation_ratio",
            "thresholds": [10.0, 20.0, 30.0, 40.0, 50.0],  # recorded as JSON
            "gap_tolerance_m": 250.0,
            "structure_threshold": 30.0,
        }
        assert config.module_params["xlma_stat"] == {
            "input_dir": str(tmp_path),
            "search_radius_m": 500.0,
        }


class TestChecks:
    """Every module the file runs is configured and wired now, before the run starts."""

    def test_a_module_missing_its_input_names_the_module_to_list(self, path):
        pipeline = _pipeline(
            modules=("ingest", "detection", "analysis", "tracking"),
            params={"detection": {"seed_carry_frames": 0}},
        )

        with pytest.raises(ConfigError) as exc:
            run_config(pipeline, path)

        message = str(exc.value)
        assert str(path) in message
        assert "'analysis'" in message
        assert "list 'projection' too" in message

    def test_a_module_missing_an_earlier_scan_output_names_the_module_to_list(self, path):
        # By default detection carries seeds along projection's labels of the scan before.
        pipeline = _pipeline(modules=("ingest", "detection"))

        with pytest.raises(ConfigError) as exc:
            run_config(pipeline, path)

        message = str(exc.value)
        assert str(path) in message
        assert "Module 'detection' reads output 'projected_ds' of earlier scans" in message
        assert "list 'projection' under modules:" in message
        assert "(adapt config --params detection)" in message

    def test_a_module_option_of_the_wrong_type_is_refused(self, path):
        params = {"cell_volume_stats": {"gap_tolerance_m": "wide"}}

        with pytest.raises(ConfigError) as exc:
            run_config(_pipeline(params=params), path)

        assert str(path) in str(exc.value)
        assert "gap_tolerance_m" in str(exc.value)

    def test_xlma_stat_without_its_input_folder_is_refused(self, path):
        with pytest.raises(ConfigError) as exc:
            run_config(_pipeline(modules=(*_MODULES, "xlma_stat")), path)

        assert str(path) in str(exc.value)
        assert "input_dir" in str(exc.value)

    def test_arm_radar_comm_needs_target_selection(self, path):
        with pytest.raises(ConfigError, match="list 'target_selection' too"):
            run_config(_pipeline(modules=(*_MODULES, "arm_radar_comm")), path)

    def test_arm_radar_comm_needs_the_cells_measured_extent_or_a_fixed_band(self, path):
        radar = ("target_selection", "arm_radar_comm")
        without_volume_stats = (*_CORE, *radar)

        with pytest.raises(ConfigError, match="list 'cell_volume_stats' too"):
            run_config(_pipeline(modules=without_volume_stats), path)

        band = {
            "extent": {
                "horizontal_buffer_km": 1.0,
                "vertical_band": {"altitude_min_m": 500.0, "altitude_max_m": 12000.0},
            }
        }
        config = run_config(
            _pipeline(modules=without_volume_stats, params={"arm_radar_comm": band}), path
        )
        assert config.modules == list(without_volume_stats)

    @pytest.mark.parametrize("variable", ["ADAPT_TEST_URL", "ADAPT_TEST_KEY", "ADAPT_TEST_CA"])
    def test_a_arm_radar_comm_that_sends_needs_its_api_settings_now(
        self, path, monkeypatch, variable
    ):
        pipeline = _pipeline(
            modules=(*_MODULES, "target_selection", "arm_radar_comm"),
            params={"arm_radar_comm": {"api": API}},
        )
        export_api(monkeypatch)
        monkeypatch.delenv(variable)

        with pytest.raises(ConfigError, match=variable):
            run_config(pipeline, path)

        export_api(monkeypatch)
        recorded = run_config(pipeline, path).module_params["arm_radar_comm"]["api"]
        assert recorded == API  # the names of the variables, never what they hold

    def test_the_target_selection_rule_is_its_packaged_one_with_the_files_parts_laid_over(
        self, path
    ):
        pipeline = _pipeline(
            modules=(*_MODULES, "target_selection"),
            params={"target_selection": {"duration": {"max_minutes": 15}}},
        )

        config = run_config(pipeline, path)

        rule = resolve_module_configs(config, ["target_selection"])["target_selection_config"].rules
        assert rule.duration.max_minutes == 15
        assert rule.switch.allow is False  # as packaged
        assert len(rule.entry.gates) == 6  # as packaged


class TestProvenance:
    def test_run_records_the_pipeline_name_and_file(self, path):
        provenance = run_config(_pipeline(), path).pipeline

        assert provenance is not None
        assert (provenance.name, provenance.file) == ("case", str(path))

    def test_run_records_the_files_validated_content(self, path):
        params = {"cell_volume_stats": {"gap_tolerance_m": 250.0}}
        pipeline = _pipeline(params=params)

        provenance = run_config(pipeline, path).pipeline

        assert provenance is not None
        assert provenance.content == pipeline.model_dump(mode="json")
        assert provenance.content["params"] == params


def _record(store_root, config, **changed) -> None:
    """Record ``config``'s run in the store, as the orchestrator does when it starts
    (``changed``: parts of the record that differ)."""
    record_run(store_root, config, **changed)


def _record_of_another_version(store_root, config) -> None:
    """Record ``config``'s run as Adapt 0.9.1 would have, were its config to hold a
    section and a downloader option this version does not have; the run has ended."""
    recorded = json.loads(config.model_dump_json())
    recorded["retired_section"] = {"depth": 2}
    recorded["downloader"]["retired_option"] = 1
    _record(store_root, config, config_json=json.dumps(recorded), pipeline_version="0.9.1")
    StoreRegistry.get_instance(store_root).finalize_run(
        config.run_id, "completed", scans_processed=0
    )


def _record_with_a_retired_module_option(store_root, config) -> None:
    """Record ``config``'s run as a version whose cell_volume_stats had one more
    option would have; the run has ended."""
    recorded = json.loads(config.model_dump_json())
    recorded["module_params"]["cell_volume_stats"]["retired_option"] = 1
    _record(store_root, config, config_json=json.dumps(recorded))
    registry_db = sqlite3.connect(store_root / "registry.db")
    try:  # what the run keeps of the module's options holds the retired one too
        options_json = json.dumps(recorded["module_params"]["cell_volume_stats"])
        registry_db.execute(
            "UPDATE run_module_provenance SET options_json = ?, options_hash = ? "
            "WHERE module = 'cell_volume_stats'",
            (options_json, options_hash(options_json)),
        )
        registry_db.commit()
    finally:
        registry_db.close()
    StoreRegistry.get_instance(store_root).finalize_run(
        config.run_id, "completed", scans_processed=0
    )


def _keep_without(store_root, module: str, option: str) -> None:
    """Make the run's kept options of ``module`` those of a version that did not
    have ``option`` yet."""
    registry_db = sqlite3.connect(store_root / "registry.db")
    try:
        (kept,) = registry_db.execute(
            "SELECT options_json FROM run_module_provenance WHERE module = ?", (module,)
        ).fetchone()
        options = json.loads(kept)
        del options[option]
        options_json = json.dumps(options)
        registry_db.execute(
            "UPDATE run_module_provenance SET options_json = ?, options_hash = ? WHERE module = ?",
            (options_json, options_hash(options_json), module),
        )
        registry_db.commit()
    finally:
        registry_db.close()


def _assert_names_the_unreadable_module_option(message: str) -> None:
    assert f"run {_RUN_ID}" in message
    assert "  • params.cell_volume_stats.retired_option — unrecognized option" in message


def _assert_names_the_unusable_record(message: str) -> None:
    assert message == (
        f"Run {_RUN_ID}: this Adapt version cannot read its recorded config "
        "(runs.config_json), at: retired_section, downloader.retired_option. The run was "
        "recorded with pipeline_version 0.9.1. It cannot be resumed or used for a stored-run "
        "pass (run.source: stored_run) with this version"
    )


class TestRunId:
    def test_new_run_gets_a_generated_id_ending_in_the_radar(self, path):
        run = {"source": "aws_nexrad", "radar": "kbox", "mode": "realtime"}

        run_id = run_config(_pipeline(run=run), path).run_id

        assert run_id is not None
        assert run_id.endswith("-KBOX")
        assert len(run_id) == len(_RUN_ID)

    def test_unknown_run_id_starts_a_new_run_with_that_id(self, path, capsys):
        config = run_config(_pipeline(), path, run_id=_RUN_ID)

        assert config.run_id == _RUN_ID
        assert config.modules == list(_MODULES)
        assert "new run" in capsys.readouterr().out

    def test_stored_run_id_reloads_that_runs_recorded_config(self, path, store_root, capsys):
        params = {"detection": {"method": "threshold", "threshold_params": {"threshold": 37.0}}}
        recorded = run_config(_pipeline(params=params), path, run_id=_RUN_ID)
        registry = StoreRegistry.get_instance(store_root)
        registry.register_collection("KBOX", source_kind="aws_nexrad")
        registry.begin_run(
            RunStart(
                run_id=_RUN_ID,
                collection_id="KBOX",
                config_hash="h",
                config_json=recorded.model_dump_json(),
                pipeline_version="0",
                environment_json="{}",
            )
        )

        config = run_config(_pipeline(), path, run_id=_RUN_ID)

        assert config == recorded
        out = capsys.readouterr().out
        assert "Continuing existing run ID" in out
        assert "max_runtime_minutes" in out  # says which part of the file still applies

    def test_a_run_continued_in_a_moved_store_writes_where_the_store_now_is(
        self, path, store_root, tmp_path
    ):
        _record(store_root, run_config(_pipeline(), path, run_id=_RUN_ID))
        moved = tmp_path / "moved"
        shutil.copytree(store_root, moved)

        config = run_config(_pipeline(), moved / path.name, run_id=_RUN_ID)

        assert config.base_dir == str(moved)

    def test_continuing_a_run_of_another_radar_is_refused(self, path, store_root):
        recorded = run_config(_pipeline(), path, run_id=_RUN_ID)
        registry = StoreRegistry.get_instance(store_root)
        registry.register_collection("KBOX", source_kind="aws_nexrad")
        registry.begin_run(
            RunStart(
                run_id=_RUN_ID,
                collection_id="KBOX",
                config_hash="h",
                config_json=recorded.model_dump_json(),
                pipeline_version="0",
                environment_json="{}",
            )
        )
        other = {"source": "aws_nexrad", "radar": "KOTH", "mode": "realtime"}

        with pytest.raises(ConfigError) as exc:
            run_config(_pipeline(run=other), path, run_id=_RUN_ID)

        assert "KBOX" in str(exc.value)
        assert "KOTH" in str(exc.value)

    def test_a_run_recorded_with_a_config_this_version_cannot_read_is_not_resumed(
        self, path, store_root
    ):
        _record_of_another_version(store_root, run_config(_pipeline(), path, run_id=_RUN_ID))

        with pytest.raises(ConfigError) as exc:
            run_config(_pipeline(), path, run_id=_RUN_ID)

        _assert_names_the_unusable_record(str(exc.value))

    def test_a_run_recorded_with_a_module_option_this_version_cannot_read_is_not_resumed(
        self, path, store_root
    ):
        _record_with_a_retired_module_option(
            store_root, run_config(_pipeline(), path, run_id=_RUN_ID)
        )

        with pytest.raises(ConfigError) as exc:
            run_config(_pipeline(), path, run_id=_RUN_ID)

        _assert_names_the_unreadable_module_option(str(exc.value))

    def test_the_refusal_of_such_a_record_does_not_guess_which_version_recorded_it(
        self, path, store_root
    ):
        """The recorded version label may equal this build's: it is told as a fact only."""
        config = run_config(_pipeline(), path, run_id=_RUN_ID)
        recorded = json.loads(config.model_dump_json())
        recorded["global_"]["z_level"] = None  # as JSON records an infinite altitude
        _record(store_root, config, config_json=json.dumps(recorded), pipeline_version=__version__)

        with pytest.raises(ConfigError) as exc:
            run_config(_pipeline(), path, run_id=_RUN_ID)

        message = str(exc.value)
        assert "another" not in message
        assert f"The run was recorded with pipeline_version {__version__}." in message
        assert "at: global_.z_level." in message

    def test_a_new_run_whose_generated_id_is_taken_is_refused(self, path, store_root, monkeypatch):
        # Ids have minute resolution: a second run started within the same minute.
        class _SameMinute(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 3, 23, 2, 6, 40)

        monkeypatch.setattr("adapt.configuration.pipeline_run.datetime", _SameMinute)
        _record(store_root, run_config(_pipeline(), path))

        with pytest.raises(ConfigError) as exc:
            run_config(_pipeline(params={"tracking": {"max_speed_ms": 30.0}}), path)

        message = str(exc.value)
        assert f"A run with id {_RUN_ID} was started this minute" in message
        assert "wait a minute" in message
        assert "--run-id" in message

    def test_a_new_run_id_must_end_in_the_files_radar(self, path):
        with pytest.raises(ConfigError) as exc:
            run_config(_pipeline(), path, run_id="2026MAR23-0206-KLOT")

        message = str(exc.value)
        assert "2026MAR23-0206-KLOT" in message
        assert "-KBOX" in message

    def test_malformed_run_id_is_rejected(self, path):
        with pytest.raises(ConfigError, match="YYYYMONDD-HHMM-RADAR"):
            run_config(_pipeline(), path, run_id="bad-run-id")

    def test_run_id_outside_an_initialized_store_is_rejected(self, tmp_path):
        with pytest.raises(StoreError, match="adapt init"):
            run_config(_pipeline(), tmp_path / "case.yaml", run_id=_RUN_ID)


class _Depth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    depth: int = 1


class _Overwrites(BaseModule):
    """A module with one option, whose rows a later pass writes over."""

    name = "overwrites_probe"
    inputs = ["scan_id"]
    outputs = ["overwrites_probe_out"]
    options_class = _Depth

    def run(self, context: dict) -> dict:
        return {}


class _KeepsApart(_Overwrites):
    """The same, but it keys its rows by its options."""

    name = "keeps_apart_probe"
    outputs = ["keeps_apart_probe_out"]
    rows_keyed_by_options = True


@pytest.fixture
def probes():
    """Two modules outside the catalog, registered for the test."""
    for cls in (_Overwrites, _KeepsApart):
        registry.register(cls)
    yield
    for cls in (_Overwrites, _KeepsApart):
        registry.unregister(cls.name)


class TestStoredRun:
    """``run.source: stored_run``: the file's modules over a run the store already has."""

    _STORED = {"source": "stored_run", "radar": "KBOX", "mode": "historical"}

    def _stored(self, path, modules=_MODULES, **params):
        """Record run ``_RUN_ID`` of a file with these modules and params in the
        store ``path`` is in, completed; return its config."""
        recorded = run_config(_pipeline(modules=modules, params=params), path, run_id=_RUN_ID)
        _record(path.parent, recorded)
        StoreRegistry.get_instance(path.parent).finalize_run(
            _RUN_ID, "completed", scans_processed=0
        )
        return recorded

    def test_it_runs_the_files_modules_with_the_stored_runs_settings(self, path, tmp_path):
        recorded = self._stored(path, **{"global": {"z_level": 3000}})
        later = _pipeline(
            run=self._STORED,
            modules=("xlma_stat",),
            params={"xlma_stat": {"input_dir": str(tmp_path)}},
        )

        config = run_config(later, path, run_id=_RUN_ID)

        assert (config.source, config.mode, config.run_id) == ("stored_run", "historical", _RUN_ID)
        assert config.modules == ["xlma_stat"]
        assert config.global_ == recorded.global_  # z_level 3000, as the run was made
        assert config.module_params["xlma_stat"]["input_dir"] == str(tmp_path)
        assert (
            config.module_params["cell_volume_stats"] == recorded.module_params["cell_volume_stats"]
        )

    def test_over_a_run_in_a_moved_store_it_writes_where_the_store_now_is(
        self, path, store_root, tmp_path
    ):
        self._stored(path)
        moved = tmp_path / "moved"
        shutil.copytree(store_root, moved)
        later = _pipeline(run=self._STORED, modules=("target_selection",))

        config = run_config(later, moved / path.name, run_id=_RUN_ID)

        assert config.base_dir == str(moved)

    def test_it_needs_the_run_named(self, path):
        self._stored(path)

        with pytest.raises(ConfigError, match="--run-id"):
            run_config(_pipeline(run=self._STORED, modules=("target_selection",)), path)

    def test_an_unknown_run_lists_the_radars_runs(self, path):
        self._stored(path)

        with pytest.raises(ConfigError) as exc:
            run_config(
                _pipeline(run=self._STORED, modules=("target_selection",)),
                path,
                run_id="2026MAR24-0000-KBOX",
            )

        assert "2026MAR24-0000-KBOX" in str(exc.value)
        assert _RUN_ID in str(exc.value)

    def test_a_run_still_running_is_refused(self, path, store_root):
        _record(store_root, run_config(_pipeline(), path, run_id=_RUN_ID))  # never ended

        with pytest.raises(ConfigError, match="still running"):
            run_config(
                _pipeline(run=self._STORED, modules=("target_selection",)), path, run_id=_RUN_ID
            )

    def test_a_run_recorded_with_a_config_this_version_cannot_read_is_refused(
        self, path, store_root
    ):
        _record_of_another_version(store_root, run_config(_pipeline(), path, run_id=_RUN_ID))
        later = _pipeline(run=self._STORED, modules=("target_selection",))

        with pytest.raises(ConfigError) as exc:
            run_config(later, path, run_id=_RUN_ID)

        _assert_names_the_unusable_record(str(exc.value))

    @pytest.mark.parametrize(
        ("section", "options"),
        [
            ("global", {"z_level": 3000}),
            ("downloader", {"poll_interval_sec": 60}),
            ("logging", {"level": "DEBUG"}),
        ],
    )
    def test_settings_every_module_shares_cannot_change(self, path, section, options):
        self._stored(path)
        later = _pipeline(
            run=self._STORED, modules=("target_selection",), params={section: options}
        )

        with pytest.raises(ConfigError, match=f"params.{section} cannot change over a stored run"):
            run_config(later, path, run_id=_RUN_ID)

    def test_a_module_may_read_what_an_unlisted_module_stored(self, path):
        """Over a live source arm_radar_comm needs target_selection listed; over a
        stored run it reads the stored selection."""
        self._stored(path)

        config = run_config(
            _pipeline(run=self._STORED, modules=("arm_radar_comm",)), path, run_id=_RUN_ID
        )

        assert config.modules == ["arm_radar_comm"]

    def test_arm_radar_comm_never_sends_over_a_stored_run(self, path, monkeypatch):
        self._stored(path)
        for variable in ("ADAPT_TEST_URL", "ADAPT_TEST_KEY", "ADAPT_TEST_CA"):
            monkeypatch.delenv(variable, raising=False)  # not needed: nothing is sent
        later = _pipeline(
            run=self._STORED,
            modules=("target_selection", "arm_radar_comm"),
            params={"arm_radar_comm": {"api": API}},
        )

        config = run_config(later, path, run_id=_RUN_ID)

        configs = resolve_module_configs(config, ["arm_radar_comm"])
        assert configs["arm_radar_comm_config"].options.api is None

    def test_a_listed_modules_options_are_its_defaults_then_the_files_not_the_stored_runs(
        self, path, store_root, monkeypatch
    ):
        """What the stored run recorded for a module the pass lists is replaced whole."""
        export_api(monkeypatch)
        sending = {"api": API, "planning": {"horizon_minutes": 6, "interval_seconds": 60}}
        recorded = run_config(
            _pipeline(
                modules=(*_MODULES, "target_selection", "arm_radar_comm"),
                params={"arm_radar_comm": sending},
            ),
            path,
            run_id=_RUN_ID,
        )
        _record(store_root, recorded)
        StoreRegistry.get_instance(store_root).finalize_run(_RUN_ID, "completed", scans_processed=0)
        later = _pipeline(run=self._STORED, modules=("arm_radar_comm",))

        config = run_config(later, path, run_id=_RUN_ID)

        assert recorded.module_params["arm_radar_comm"]["api"] == API
        assert config.module_params["arm_radar_comm"]["api"] is None
        assert config.module_params["arm_radar_comm"]["planning"]["horizon_minutes"] == 12
        assert (
            config.module_params["target_selection"] == recorded.module_params["target_selection"]
        )

    @pytest.mark.parametrize(
        ("module", "ran_with", "given", "differing"),
        [
            pytest.param(
                "cell_volume_stats",
                {"gap_tolerance_m": 250.0},
                {},
                ["params.cell_volume_stats.gap_tolerance_m — recorded: 250.0; this file: 500.0"],
                id="the file leaves an option at its default",
            ),
            pytest.param(
                "cell_volume_stats",
                {},
                {"thresholds": [30, 45.5], "structure_threshold": 35},
                [
                    "params.cell_volume_stats.thresholds — recorded: "
                    "[10.0, 20.0, 30.0, 40.0, 50.0]; this file: [30.0, 45.5]",
                    "params.cell_volume_stats.structure_threshold — recorded: 30.0; "
                    "this file: 35.0",
                ],
                id="the file changes two options",
            ),
            pytest.param(
                "xlma_stat",
                {"search_radius_m": 750.0},
                {},
                ["params.xlma_stat.search_radius_m — recorded: 750.0; this file: 500.0"],
                id="another module",
            ),
        ],
    )
    def test_a_module_the_stored_run_ran_with_other_options_is_refused(
        self, path, tmp_path, module, ran_with, given, differing
    ):
        """Its rows would be written over in part: rows of both options, mixed."""

        def params(options: dict) -> dict:
            both = {"xlma_stat": {"input_dir": str(tmp_path)}}  # the same folder then and now
            return {**both, module: {**both.get(module, {}), **options}}

        self._stored(path, modules=(*_MODULES, "xlma_stat"), **params(ran_with))
        later = _pipeline(
            run=self._STORED, modules=("cell_volume_stats", "xlma_stat"), params=params(given)
        )

        with pytest.raises(ConfigError) as exc:
            run_config(later, path, run_id=_RUN_ID)

        first, *options, last = str(exc.value).splitlines()
        assert first == (
            f"Pipeline file {path}: run {_RUN_ID} ran {module} with other options than "
            "this file gives:"
        )
        assert options == [f"  • {line}" for line in differing]
        assert f"Give {module} the recorded options" in last
        assert "or start a new run" in last

    def test_a_module_given_the_options_the_stored_run_recorded_runs_over_it_again(
        self, path, tmp_path
    ):
        ran_with = {"xlma_stat": {"input_dir": str(tmp_path), "search_radius_m": 750.0}}
        recorded = self._stored(path, modules=(*_MODULES, "xlma_stat"), **ran_with)
        later = _pipeline(run=self._STORED, modules=("xlma_stat",), params=ran_with)

        config = run_config(later, path, run_id=_RUN_ID)

        assert config.modules == ["xlma_stat"]
        assert config.module_params == recorded.module_params

    def test_target_selection_may_run_with_another_rule_than_the_stored_run_recorded(self, path):
        """Its rows carry the rule's hash in their key: both rules' rows are kept."""
        self._stored(
            path,
            modules=(*_MODULES, "target_selection"),
            target_selection={"duration": {"max_minutes": 15}},
        )
        later = _pipeline(run=self._STORED, modules=("target_selection",))

        config = run_config(later, path, run_id=_RUN_ID)

        assert config.module_params["target_selection"]["duration"]["max_minutes"] == 60.0

    def test_a_module_that_keys_its_rows_by_its_options_may_run_with_other_options(
        self, path, probes
    ):
        self._stored(
            path,
            modules=(*_MODULES, _KeepsApart.name),
            **{_KeepsApart.name: {"depth": 2}},
        )
        later = _pipeline(run=self._STORED, modules=(_KeepsApart.name,))

        config = run_config(later, path, run_id=_RUN_ID)

        assert config.module_params[_KeepsApart.name] == {"depth": 1}

    def test_a_module_that_does_not_say_so_is_refused_other_options(self, path, probes):
        self._stored(
            path,
            modules=(*_MODULES, _Overwrites.name),
            **{_Overwrites.name: {"depth": 2}},
        )
        later = _pipeline(run=self._STORED, modules=(_Overwrites.name,))

        with pytest.raises(ConfigError) as exc:
            run_config(later, path, run_id=_RUN_ID)

        assert "  • params.overwrites_probe.depth — recorded: 2; this file: 1" in str(exc.value)

    def test_recorded_options_this_version_cannot_read_refuse_a_pass_of_that_module(
        self, path, store_root
    ):
        """Whether the pass gives the module the options it ran with cannot be told."""
        _record_with_a_retired_module_option(
            store_root, run_config(_pipeline(), path, run_id=_RUN_ID)
        )
        later = _pipeline(run=self._STORED, modules=("cell_volume_stats",))

        with pytest.raises(ConfigError) as exc:
            run_config(later, path, run_id=_RUN_ID)

        _assert_names_the_unreadable_module_option(str(exc.value))

    def _stored_before_the_search_radius(self, path, tmp_path):
        """A stored run that ran ``xlma_stat`` when it had no ``search_radius_m``
        yet; returns the run's config and the options a file gives the module."""
        ran_with = {"xlma_stat": {"input_dir": str(tmp_path)}}
        recorded = self._stored(path, modules=(*_MODULES, "xlma_stat"), **ran_with)
        _keep_without(path.parent, "xlma_stat", "search_radius_m")
        return recorded, ran_with

    def test_a_module_that_gained_an_option_since_the_run_may_run_over_it_again(
        self, path, tmp_path
    ):
        """The run kept the module's options without the new one; this version
        reads them with the new option at its default, as it would resume the run."""
        _, ran_with = self._stored_before_the_search_radius(path, tmp_path)
        later = _pipeline(run=self._STORED, modules=("xlma_stat",), params=ran_with)

        config = run_config(later, path, run_id=_RUN_ID)

        assert config.modules == ["xlma_stat"]

    def test_a_module_that_gained_an_option_is_still_refused_other_options(self, path, tmp_path):
        self._stored_before_the_search_radius(path, tmp_path)
        other = {"xlma_stat": {"input_dir": str(tmp_path), "search_radius_m": 750.0}}
        later = _pipeline(run=self._STORED, modules=("xlma_stat",), params=other)

        with pytest.raises(ConfigError) as exc:
            run_config(later, path, run_id=_RUN_ID)

        assert "  • params.xlma_stat.search_radius_m — recorded: " in str(exc.value)

    def test_options_kept_twice_since_an_option_was_added_are_one_set_of_options(
        self, path, tmp_path
    ):
        """The run kept them without the new option, and again with it at its
        default when this version resumed the run."""
        recorded, ran_with = self._stored_before_the_search_radius(path, tmp_path)
        StoreRegistry.get_instance(path.parent).record_module_provenance(
            _RUN_ID, module_provenance(recorded)
        )
        later = _pipeline(run=self._STORED, modules=("xlma_stat",), params=ran_with)

        config = run_config(later, path, run_id=_RUN_ID)

        assert config.modules == ["xlma_stat"]

    def test_the_wrong_options_of_several_listed_modules_are_told_together(self, path, tmp_path):
        self._stored(path)
        later = _pipeline(
            run=self._STORED,
            modules=("xlma_stat", "target_selection"),
            params={
                "xlma_stat": {"input_dir": str(tmp_path), "no_such_option": 1},
                "target_selection": {"no_such_part": {}},
            },
        )

        with pytest.raises(ConfigError) as exc:
            run_config(later, path, run_id=_RUN_ID)

        first, *told = str(exc.value).splitlines()
        assert first == f"Invalid configuration (pipeline file {path}):"
        assert [line.split(" — ")[0] for line in told] == [
            "  • params.xlma_stat.no_such_option",
            "  • params.target_selection.no_such_part",
            "The options of xlma_stat: adapt config --params xlma_stat",
            "The options of target_selection: adapt config --params target_selection",
        ]
