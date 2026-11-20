# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""A module's options: its defaults with the pipeline file's ``params.<module>`` laid over.

For the modules that own their options (cell_volume_stats, xlma_stat,
target_selection, radar_command): which config each gets from a pipeline file,
how a wrong option is refused, and what a run records of them. The expected
configs are written out by hand, from the packaged defaults.
"""

import copy
import json
from importlib.resources import files

import pytest
import yaml

from adapt.configuration.pipeline_file import PipelineFile
from adapt.configuration.pipeline_run import configured_modules, run_config
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.module_resolver import module_options, resolve_module_configs
from adapt.contracts import config_key
from adapt.execution.module_registry import registry
from adapt.modules.analysis.config import AnalysisOptions
from adapt.modules.base import BaseModule
from adapt.modules.cell_volume_stats.config import CellVolumeStatsConfig
from adapt.modules.detection.config import DetectionOptions
from adapt.modules.ingest.config import IngestOptions
from adapt.modules.projection.config import ProjectionOptions
from adapt.modules.radar_command import RadarCommandConfig
from adapt.modules.target_selection import TSEConfig
from adapt.modules.target_selection.node import TargetSelectionRun
from adapt.modules.tracking.config import TrackingOptions
from adapt.modules.xlma_stat.config import XlmaStatConfig
from adapt.persistence.store import init_store
from tests.helpers.pipeline_files import API, export_api

pytestmark = pytest.mark.unit

_CORE = ("ingest", "detection", "projection", "analysis", "tracking")
_OWNERS = ("cell_volume_stats", "xlma_stat", "target_selection", "radar_command")
_BAND = {"altitude_min_m": 500.0, "altitude_max_m": 12000.0}
_GATE = {"field": "n_scans", "op": "ge", "value": 3}


def _packaged(module: str, preset: str) -> dict:
    """The options of ``module``'s packaged preset, as the YAML file gives them."""
    text = (files(f"adapt.modules.{module}") / "presets" / f"{preset}.yaml").read_text("utf-8")
    return copy.deepcopy(yaml.safe_load(text)[module])


def _rule(**parts) -> TSEConfig:
    """The packaged target selection rule with these parts in place of its own."""
    return TSEConfig.model_validate({**_packaged("target_selection", "growing_isolated"), **parts})


def _command(**parts) -> RadarCommandConfig:
    """The packaged radar command options with these parts in place of their own."""
    return RadarCommandConfig.model_validate({**_packaged("radar_command", "sandbox"), **parts})


def _volume_stats(**options) -> CellVolumeStatsConfig:
    """cell_volume_stats' config with every option at its default but these."""
    return CellVolumeStatsConfig(
        **{
            "reflectivity_var": "reflectivity",
            "zdr_var": "differential_reflectivity",
            "kdp_var": "specific_differential_phase",
            "rhohv_var": "cross_correlation_ratio",
            "thresholds": (10.0, 20.0, 30.0, 40.0, 50.0),
            "gap_tolerance_m": 500.0,
            "structure_threshold": 30.0,
            **options,
        }
    )


@pytest.fixture
def path(tmp_path):
    """A pipeline file's place in a store; its folder holds one flash-sorted file."""
    root = init_store(tmp_path / "store")
    (root / "flashes.nc").write_bytes(b"")
    return root / "case.yaml"


def _pipeline(path, params: dict) -> PipelineFile:
    """A run of every module; xlma_stat reads the file's own folder unless told otherwise."""
    params = {name: dict(options) for name, options in params.items()}
    params.setdefault("xlma_stat", {}).setdefault("input_dir", str(path.parent))
    return PipelineFile.model_validate(
        {
            "run": {"source": "aws_nexrad", "radar": "KBOX", "mode": "realtime"},
            "modules": [*_CORE, *_OWNERS],
            "params": params,
        }
    )


def _config(path, module: str, params: dict):
    """The config ``module`` gets from a new run of a file with these ``params``."""
    config = run_config(_pipeline(path, params), path)
    return resolve_module_configs(config, [module])[config_key(module)]


def _refusal(path, params: dict) -> list[str]:
    """The lines of the refusal of a file with these ``params``."""
    with pytest.raises(ConfigError) as exc:
        run_config(_pipeline(path, params), path)
    return str(exc.value).splitlines()


_SITES = {"projection_steps": 3, "sites": []}  # the packaged score.site_preference
_WEIGHTS = {  # the packaged score.weights
    "trend_area_40dbz_km2_per_min": 10.0,
    "radar_differential_reflectivity_max": 5.0,
    "n_adjacent_cells": -10.0,
}


class TestConfigEachModuleGets:
    def test_without_params_cell_volume_stats_runs_on_its_defaults_and_the_tracking_field(
        self, path
    ):
        assert _config(path, "cell_volume_stats", {}) == _volume_stats()

    def test_cell_volume_stats_options_replace_their_defaults_only(self, path):
        params = {"cell_volume_stats": {"thresholds": [30, 45.5], "gap_tolerance_m": 250}}

        assert _config(path, "cell_volume_stats", params) == _volume_stats(
            thresholds=(30.0, 45.5), gap_tolerance_m=250.0
        )

    def test_cell_volume_stats_reads_the_field_the_run_tracks(self, path):
        params = {"global": {"tracking_field": "velocity"}}

        assert _config(path, "cell_volume_stats", params) == _volume_stats(
            reflectivity_var="velocity"
        )

    def test_xlma_stat_options_replace_their_defaults_only(self, path, tmp_path):
        (tmp_path / "flashes.nc").write_bytes(b"")
        params = {"xlma_stat": {"input_dir": str(tmp_path)}}

        assert _config(path, "xlma_stat", params) == XlmaStatConfig(
            input_dir=str(tmp_path), search_radius_m=500.0
        )

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            pytest.param({}, {}, id="nothing: the packaged rule"),
            pytest.param(
                {"duration": {"max_minutes": 15}},
                {"duration": {"max_minutes": 15}},
                id="one key of a part",
            ),
            pytest.param(
                {"duration": {"max_minutes": None}},
                {"duration": {"max_minutes": None}},
                id="a null for an option that may be null",
            ),
            pytest.param(
                {"snapshot": {"track_summary": "end_of_run"}},
                {
                    "snapshot": {
                        "growth_window_scans": 2,
                        "trend_fields": ["area_40dbz_km2", "radar_reflectivity_max"],
                        "track_summary": "end_of_run",
                    }
                },
                id="one key of a part with three",
            ),
            pytest.param(
                {"score": {"weights": {"cell_area_sqkm": 0.5}}},
                {"score": {"weights": {"cell_area_sqkm": 0.5}, "site_preference": _SITES}},
                id="weights are replaced whole, not merged",
            ),
            pytest.param(
                {"score": {"weights": {}}},
                {"score": {"weights": {}, "site_preference": _SITES}},
                id="empty weights replace the packaged ones",
            ),
            pytest.param(
                {"entry": {"gates": [_GATE]}},
                {"entry": {"gates": [_GATE]}},
                id="a list is replaced whole",
            ),
            pytest.param(
                {"score": {"site_preference": {"projection_steps": 1, "sites": []}}},
                {
                    "score": {
                        "weights": _WEIGHTS,
                        "site_preference": {"projection_steps": 1, "sites": []},
                    }
                },
                id="a whole nested part",
            ),
        ],
    )
    def test_target_selection_gets_its_packaged_rule_with_the_files_parts_laid_over(
        self, path, given, expected
    ):
        config = _config(path, "target_selection", {"target_selection": given})

        assert config == TargetSelectionRun(rules=_rule(**expected), tracking_field="reflectivity")

    def test_one_key_deep_inside_a_nested_part_may_be_given_alone(self, path):
        given = {"score": {"site_preference": {"projection_steps": 1}}}

        config = _config(path, "target_selection", {"target_selection": given})

        score = {"weights": _WEIGHTS, "site_preference": {"projection_steps": 1, "sites": []}}
        assert config.rules == _rule(score=score)

    def test_target_selection_scores_the_field_the_run_tracks(self, path):
        config = _config(path, "target_selection", {"global": {"tracking_field": "velocity"}})

        assert config == TargetSelectionRun(rules=_rule(), tracking_field="velocity")

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            pytest.param({}, {}, id="nothing: the packaged options"),
            pytest.param(
                {"planning": {"horizon_minutes": 6}},
                {"planning": {"horizon_minutes": 6, "interval_seconds": 60}},
                id="one key of a part",
            ),
            pytest.param({"api": None}, {"api": None}, id="a null api: a dry run"),
            pytest.param({"api": API}, {"api": API}, id="an api part the packaged options lack"),
            pytest.param(
                {"extent": {"vertical_band": _BAND}},
                {"extent": {"horizontal_buffer_km": 2.0, "vertical_band": _BAND}},
                id="an optional nested part",
            ),
            pytest.param(
                {"extent": {"vertical_band": None}},
                {"extent": {"horizontal_buffer_km": 2.0, "vertical_band": None}},
                id="a null for an optional nested part",
            ),
            pytest.param(
                {"radar": {"user_id": "someone"}, "extent": {"horizontal_buffer_km": 0}},
                {
                    "radar": {"scan_pattern": "rhi3_ppi7", "user_id": "someone"},
                    "extent": {"horizontal_buffer_km": 0.0},
                },
                id="keys of two parts",
            ),
        ],
    )
    def test_radar_command_gets_its_packaged_options_with_the_files_parts_laid_over(
        self, path, monkeypatch, given, expected
    ):
        export_api(monkeypatch)

        config = _config(path, "radar_command", {"radar_command": given})

        assert config == _command(**expected)


class TestRefusals:
    """One line per option, ``params.<module>.<option> — <why>``, under the file's name."""

    def test_the_refusal_names_the_pipeline_file(self, path):
        lines = _refusal(path, {"cell_volume_stats": {"gap_tolerance_m": -1}})

        assert lines[0] == f"Invalid configuration (pipeline file {path}):"

    @pytest.mark.parametrize(
        ("params", "line"),
        [
            pytest.param(
                {"cell_volume_stats": {"gap_tolerance_m": -1}},
                "params.cell_volume_stats.gap_tolerance_m — value -1 is out of range — "
                "Input should be greater than or equal to 0",
                id="out of range",
            ),
            pytest.param(
                {"xlma_stat": {"search_radius": 750.0}},
                "params.xlma_stat.search_radius — unrecognized option (value: 750.0) — not a "
                "valid 'xlma_stat' setting; remove it or check for a typo or a renamed field",
                id="unknown option",
            ),
            pytest.param(
                {"cell_volume_stats": {"reflectivity_var": "dbz"}},
                "params.cell_volume_stats.reflectivity_var — unrecognized option (value: 'dbz') "
                "— not a valid 'cell_volume_stats' setting; remove it or check for a typo or a "
                "renamed field",
                id="a value every module shares is not a module's option",
            ),
            pytest.param(
                {"cell_volume_stats": {"thresholds": None}},
                "params.cell_volume_stats.thresholds — must be a list (one '- item' line per "
                "entry)",
                id="a null where a list is needed",
            ),
            pytest.param(
                {"radar_command": {"planning": {"horizon_minutes": 0}}},
                "params.radar_command.planning.horizon_minutes — value 0 is out of range — "
                "Input should be greater than or equal to 1",
                id="out of range, in a part",
            ),
            pytest.param(
                {"target_selection": {"switch": {"alow": True}}},
                "params.target_selection.switch.alow — unrecognized option (value: True) — not "
                "a valid 'switch' setting; remove it or check for a typo or a renamed field",
                id="unknown option, in a part",
            ),
            pytest.param(
                {"target_selection": {"entry": {"gates": [{**_GATE, "valeu": 1}]}}},
                "params.target_selection.entry.gates.0.valeu — unrecognized option (value: 1) "
                "— not a valid 'params.target_selection.entry.gates.0' setting; remove it or "
                "check for a typo or a renamed field",
                id="unknown option, in an item of a list",
            ),
            pytest.param(
                {"target_selection": {"entree": {}}},
                "params.target_selection.entree — unrecognized option — not a valid "
                "'target_selection' setting; remove it or check for a typo or a renamed field",
                id="unknown part",
            ),
            pytest.param(
                {"target_selection": {"duration": None}},
                "params.target_selection.duration — must be a mapping of options (key: value "
                "lines indented under it)",
                id="a null where a part is needed",
            ),
            pytest.param(
                {"radar_command": {"api": {"base_url_env": "ADAPT_TEST_URL"}}},
                "params.radar_command.api.api_key_env — required option is missing",
                id="a part given without a required option",
            ),
        ],
    )
    def test_a_wrong_option_is_named_with_the_reason(self, path, params, line):
        assert f"  • {line}" in _refusal(path, params)

    @pytest.mark.parametrize("module", ["cell_volume_stats", "target_selection"])
    def test_the_refusal_ends_with_how_to_see_the_modules_options(self, path, module):
        lines = _refusal(path, {module: {"no_such_option": 1}})

        assert lines[-1] == f"The options of {module}: adapt config --show {module}"

    def test_a_file_is_refused_without_a_store_as_adapt_graph_checks_it(self, tmp_path):
        path = tmp_path / "nexrad.yaml"
        pipeline = _pipeline(path, {"cell_volume_stats": {"gap_tolerance_m": -1}})

        with pytest.raises(ConfigError) as exc:
            configured_modules(pipeline, path)

        assert "params.cell_volume_stats.gap_tolerance_m — value -1" in str(exc.value)


class _Plain(BaseModule):
    """A module without options."""

    name = "plain_probe"

    def run(self, context: dict) -> dict:
        return {}


@pytest.fixture
def plain_module():
    registry.register(_Plain)
    yield _Plain.name
    registry.unregister(_Plain.name)


class TestModuleWithoutOptions:
    def test_it_has_no_options(self, plain_module):
        assert module_options(plain_module, {}, source="a test") is None

    def test_an_option_given_to_it_is_refused(self, plain_module):
        with pytest.raises(ConfigError) as exc:
            module_options(plain_module, {"depth": 2}, source="a test")

        assert "  • params.plain_probe.depth — unrecognized option (value: 2)" in str(exc.value)

    def test_the_refusal_says_how_to_see_that_it_has_none(self, plain_module):
        with pytest.raises(ConfigError) as exc:
            module_options(plain_module, {"depth": 2}, source="a test")

        assert str(exc.value).splitlines()[-1] == (
            "The options of plain_probe: adapt config --show plain_probe"
        )


_RECORDED_SHAPES = [
    ("cell_volume_stats", {}),
    ("cell_volume_stats", {"thresholds": [30, 45.5], "gap_tolerance_m": 250}),
    ("xlma_stat", {"input_dir": "/flashes", "search_radius_m": 0}),
    ("target_selection", {}),
    ("target_selection", {"duration": {"max_minutes": None}}),
    ("target_selection", {"score": {"weights": {}}}),
    ("target_selection", {"score": {"site_preference": {"projection_steps": 1}}}),
    ("target_selection", {"entry": {"gates": [_GATE]}, "hold": {"gates": [_GATE]}}),
    ("radar_command", {}),
    ("radar_command", {"api": None}),
    ("radar_command", {"api": API, "extent": {"vertical_band": _BAND}}),
    ("radar_command", {"planning": {"horizon_minutes": 10, "interval_seconds": 120}}),
]


class TestRunRecord:
    """A run records every listed module's options, complete, so it can be configured again."""

    def test_a_run_records_the_options_every_listed_module_runs_with(self, path, monkeypatch):
        export_api(monkeypatch)
        params = {
            "cell_volume_stats": {"gap_tolerance_m": 250.0},
            "radar_command": {"planning": {"horizon_minutes": 6}},
        }

        recorded = run_config(_pipeline(path, params), path).module_params

        assert recorded == {
            "ingest": IngestOptions().model_dump(mode="json"),
            "detection": DetectionOptions().model_dump(mode="json"),
            "projection": ProjectionOptions().model_dump(mode="json"),
            "analysis": AnalysisOptions().model_dump(mode="json"),
            "tracking": TrackingOptions().model_dump(mode="json"),
            "cell_volume_stats": {
                "zdr_var": "differential_reflectivity",
                "kdp_var": "specific_differential_phase",
                "rhohv_var": "cross_correlation_ratio",
                "thresholds": [10.0, 20.0, 30.0, 40.0, 50.0],
                "gap_tolerance_m": 250.0,
                "structure_threshold": 30.0,
            },
            "xlma_stat": {"input_dir": str(path.parent), "search_radius_m": 500.0},
            "target_selection": _rule().model_dump(mode="json"),
            "radar_command": {
                "planning": {"horizon_minutes": 6, "interval_seconds": 60},
                "radar": {"scan_pattern": "rhi3_ppi7", "user_id": "adapt_sandbox"},
                "extent": {"horizontal_buffer_km": 2.0, "vertical_band": None},
                "api": None,
            },
        }

    @pytest.mark.parametrize(("module", "given"), _RECORDED_SHAPES)
    def test_recorded_options_resolve_to_the_same_options(self, module, given):
        options = module_options(module, given, source="a test")
        recorded = json.loads(options.model_dump_json())  # as runs.config_json holds them

        assert module_options(module, recorded, source="a test") == options

    def test_a_run_read_back_from_its_record_configures_its_modules_the_same(
        self, path, monkeypatch
    ):
        export_api(monkeypatch)
        params = {
            "cell_volume_stats": {"thresholds": [30, 45.5]},
            "target_selection": {"duration": {"max_minutes": None}, "score": {"weights": {}}},
            "radar_command": {"api": API, "extent": {"vertical_band": _BAND}},
        }
        config = run_config(_pipeline(path, params), path)

        stored = type(config).model_validate_json(config.model_dump_json())  # runs.config_json

        assert stored.module_params == config.module_params
        assert resolve_module_configs(stored, _OWNERS) == resolve_module_configs(config, _OWNERS)

    @pytest.mark.parametrize("value", [float("inf"), float("nan")])
    def test_a_value_the_run_record_cannot_hold_is_refused(self, path, value):
        """JSON has no infinity: recorded, the gate would not configure the run again."""
        gate = {"field": "n_scans", "op": "le", "value": value}

        lines = _refusal(path, {"target_selection": {"hold": {"gates": [gate]}}})

        assert any(
            line.startswith("  • params.target_selection.hold.gates.0.value — ") for line in lines
        )

    def test_the_refusal_of_such_a_value_names_the_pipeline_file(self, path):
        gate = {"field": "n_scans", "op": "le", "value": float("inf")}

        lines = _refusal(path, {"target_selection": {"hold": {"gates": [gate]}}})

        assert lines[0].startswith(f"Invalid configuration (pipeline file {path}, ")

    def test_such_a_value_is_refused_for_an_option_that_may_be_empty_too(self, path):
        """Recorded, the cap would be null: the run would go on without it, silently."""
        lines = _refusal(path, {"target_selection": {"duration": {"max_minutes": float("inf")}}})

        assert lines[0] == (
            f"Invalid configuration (pipeline file {path}, as the run records it — "
            "JSON holds no infinity or NaN):"
        )
        assert lines[1].startswith("  • params.target_selection.duration.max_minutes — value inf ")

    def test_such_a_value_is_told_with_the_files_other_wrong_options(self, path):
        """One refusal: each wrong option under what holds it, the file or its record."""
        lines = _refusal(
            path,
            {
                "tracking": {"max_speed_ms": 0},
                "target_selection": {"duration": {"max_minutes": float("inf")}},
                "global": {"max_scan_gap_minutes": 0},
            },
        )

        out_of_range = "is out of range — Input should be greater than 0"
        assert lines == [
            f"Invalid configuration (pipeline file {path}):",
            f"  • params.tracking.max_speed_ms — value 0 {out_of_range}",
            f"  • params.global.max_scan_gap_minutes — value 0 {out_of_range}",
            f"Invalid configuration (pipeline file {path}, as the run records it — "
            "JSON holds no infinity or NaN):",
            "  • params.target_selection.duration.max_minutes — value inf would be read back "
            "as null; give a finite number, or leave the option empty if it may be",
            "The options of tracking: adapt config --show tracking",
        ]
