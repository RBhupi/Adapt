# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``run_config``: a pipeline file becomes the run's InternalConfig.

``run:`` maps onto the source and downloader settings, ``modules`` is the run's
module list, ``params:`` sections merge over the defaults and module-owned
params route to ``module_params``, defaults filled in. The file's
name, path and content are recorded with the run. A ``--run-id`` naming a stored
run reloads that run's recorded config; anything else is a new run. The file's
folder is the store root.
"""

import shutil
from datetime import UTC, datetime

import pytest

from adapt.configuration.pipeline_file import PipelineFile
from adapt.configuration.pipeline_run import run_config
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.param import ParamConfig
from adapt.execution.module_registry import registry
from adapt.persistence.errors import StoreError
from adapt.persistence.store import init_store
from adapt.persistence.store_registry import RunStart, StoreRegistry

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


class TestModules:
    def test_the_runs_modules_are_the_files_in_file_order(self, path):
        pipeline = _pipeline(modules=("tracking", *_MODULES[:-2], "cell_volume_stats"))

        config = run_config(pipeline, path)

        assert config.modules == ["tracking", *_MODULES[:-2], "cell_volume_stats"]


class TestParams:
    def test_section_options_override_their_defaults_only(self, path):
        params = {"segmenter": {"method": "threshold", "threshold_params": {"threshold": 42.0}}}

        config = run_config(_pipeline(params=params), path)

        defaults = ParamConfig()
        assert config.segmenter.threshold_params.threshold == 42.0
        assert config.segmenter.min_cellsize_gridpoint == defaults.segmenter.min_cellsize_gridpoint
        assert config.tracker.max_link_cost == defaults.tracker.max_link_cost

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

        cvs = registry.get("cell_volume_stats").default_params()
        assert config.module_params["cell_volume_stats"] == {
            **cvs,
            "thresholds": list(cvs["thresholds"]),  # recorded as JSON
            "gap_tolerance_m": 250.0,
        }
        assert config.module_params["xlma_stat"] == {
            **registry.get("xlma_stat").default_params(),
            "input_dir": str(tmp_path),
        }


class TestChecks:
    """Every module the file runs is configured and wired now, before the run starts."""

    def test_a_module_missing_its_input_names_the_module_to_list(self, path):
        pipeline = _pipeline(
            modules=("ingest", "detection", "analysis", "tracking"),
            params={"segmenter": {"seed_carry_frames": 0}},
        )

        with pytest.raises(ConfigError) as exc:
            run_config(pipeline, path)

        message = str(exc.value)
        assert str(path) in message
        assert "'analysis'" in message
        assert "list 'projection' too" in message

    def test_a_module_refusing_its_options_fails_the_file(self, path):
        # Seed carry advects projection's labels, so detection refuses it without them.
        pipeline = _pipeline(modules=("ingest", "detection"))

        with pytest.raises(ConfigError) as exc:
            run_config(pipeline, path)

        assert str(path) in str(exc.value)
        assert "seed_carry_frames" in str(exc.value)

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

    def test_radar_command_needs_target_selection(self, path):
        with pytest.raises(ConfigError, match="list 'target_selection' too"):
            run_config(_pipeline(modules=(*_MODULES, "radar_command")), path)

    def test_radar_command_needs_the_cells_measured_extent_or_a_fixed_band(self, path):
        radar = ("target_selection", "radar_command")
        without_volume_stats = (*_CORE, *radar)

        with pytest.raises(ConfigError, match="list 'cell_volume_stats' too"):
            run_config(_pipeline(modules=without_volume_stats), path)

        band = {"extent": {"vertical_band": {"altitude_min_m": 500.0, "altitude_max_m": 12000.0}}}
        config = run_config(
            _pipeline(modules=without_volume_stats, params={"radar_command": band}), path
        )
        assert config.modules == list(without_volume_stats)

    def test_a_radar_command_that_sends_needs_its_api_key_now(self, path, monkeypatch):
        api = {"base_url": "https://radar.example.test", "api_key_env": "ADAPT_TEST_KEY"}
        api["timeout_seconds"] = 5
        pipeline = _pipeline(
            modules=(*_MODULES, "target_selection", "radar_command"),
            params={"radar_command": {"api": api}},
        )
        monkeypatch.delenv("ADAPT_TEST_KEY", raising=False)

        with pytest.raises(ConfigError, match="ADAPT_TEST_KEY"):
            run_config(pipeline, path)

        monkeypatch.setenv("ADAPT_TEST_KEY", "a-test-key")
        assert run_config(pipeline, path).module_params["radar_command"]["api"] == api

    def test_the_target_selection_rule_is_its_packaged_one_with_the_files_parts_laid_over(
        self, path
    ):
        pipeline = _pipeline(
            modules=(*_MODULES, "target_selection"),
            params={"target_selection": {"duration": {"max_minutes": 15}}},
        )

        config = run_config(pipeline, path)

        rule = registry.get("target_selection").build_config(config).rules
        packaged = registry.get("target_selection").default_params()
        assert rule.duration.max_minutes == 15
        assert rule.switch.allow is packaged["switch"]["allow"]
        assert len(rule.entry.gates) == len(packaged["entry"]["gates"])


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


def _record(store_root, config) -> None:
    """Record ``config``'s run in the store, as the orchestrator does when it starts."""
    registry = StoreRegistry.get_instance(store_root)
    registry.register_collection(config.downloader.radar, source_kind="aws_nexrad")
    registry.begin_run(
        RunStart(
            run_id=config.run_id,
            collection_id=config.downloader.radar,
            config_hash="h",
            config_json=config.model_dump_json(),
            pipeline_version="0",
            environment_json="{}",
        )
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
        params = {"segmenter": {"method": "threshold", "threshold_params": {"threshold": 37.0}}}
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

    def test_a_new_run_whose_generated_id_is_taken_is_refused(self, path, store_root, monkeypatch):
        # Ids have minute resolution: a second run started within the same minute.
        class _SameMinute(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 3, 23, 2, 6, 40)

        monkeypatch.setattr("adapt.configuration.pipeline_run.datetime", _SameMinute)
        _record(store_root, run_config(_pipeline(), path))

        with pytest.raises(ConfigError) as exc:
            run_config(_pipeline(params={"tracker": {"max_speed_ms": 30.0}}), path)

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


class TestStoredRun:
    """``run.source: stored_run``: the file's modules over a run the store already has."""

    _STORED = {"source": "stored_run", "radar": "KBOX", "mode": "historical"}

    def _stored(self, store_root, path, status="completed", **params):
        recorded = run_config(_pipeline(params=params), path, run_id=_RUN_ID)
        _record(store_root, recorded)
        if status != "running":
            StoreRegistry.get_instance(store_root).finalize_run(_RUN_ID, status, scans_processed=0)
        return recorded

    def test_it_runs_the_files_modules_with_the_stored_runs_settings(
        self, path, store_root, tmp_path
    ):
        recorded = self._stored(store_root, path, **{"global": {"z_level": 3000}})
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
        self._stored(store_root, path)
        moved = tmp_path / "moved"
        shutil.copytree(store_root, moved)
        later = _pipeline(run=self._STORED, modules=("target_selection",))

        config = run_config(later, moved / path.name, run_id=_RUN_ID)

        assert config.base_dir == str(moved)

    def test_it_needs_the_run_named(self, path, store_root):
        self._stored(store_root, path)

        with pytest.raises(ConfigError, match="--run-id"):
            run_config(_pipeline(run=self._STORED, modules=("target_selection",)), path)

    def test_an_unknown_run_lists_the_radars_runs(self, path, store_root):
        self._stored(store_root, path)

        with pytest.raises(ConfigError) as exc:
            run_config(
                _pipeline(run=self._STORED, modules=("target_selection",)),
                path,
                run_id="2026MAR24-0000-KBOX",
            )

        assert "2026MAR24-0000-KBOX" in str(exc.value)
        assert _RUN_ID in str(exc.value)

    def test_a_run_still_running_is_refused(self, path, store_root):
        self._stored(store_root, path, status="running")

        with pytest.raises(ConfigError, match="still running"):
            run_config(
                _pipeline(run=self._STORED, modules=("target_selection",)), path, run_id=_RUN_ID
            )

    def test_settings_the_stored_run_was_made_with_cannot_change(self, path, store_root):
        self._stored(store_root, path)
        later = _pipeline(
            run=self._STORED,
            modules=("target_selection",),
            params={"tracker": {"max_speed_ms": 30.0}},
        )

        with pytest.raises(ConfigError, match="params.tracker cannot change over a stored run"):
            run_config(later, path, run_id=_RUN_ID)

    def test_a_module_may_read_what_an_unlisted_module_stored(self, path, store_root):
        """Over a live source radar_command needs target_selection listed; over a
        stored run it reads the stored selection."""
        self._stored(store_root, path)

        config = run_config(
            _pipeline(run=self._STORED, modules=("radar_command",)), path, run_id=_RUN_ID
        )

        assert config.modules == ["radar_command"]

    def test_radar_command_never_sends_over_a_stored_run(self, path, store_root, monkeypatch):
        self._stored(store_root, path)
        monkeypatch.delenv("ADAPT_TEST_KEY", raising=False)  # not needed: nothing is sent
        api = {"base_url": "https://radar.example.test", "api_key_env": "ADAPT_TEST_KEY"}
        api["timeout_seconds"] = 5
        later = _pipeline(
            run=self._STORED,
            modules=("target_selection", "radar_command"),
            params={"radar_command": {"api": api}},
        )

        config = run_config(later, path, run_id=_RUN_ID)

        assert registry.get("radar_command").build_config(config).api is None
