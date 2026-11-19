# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The run config's ``global``, ``downloader`` and ``logging`` settings.

A pipeline file sets them under ``params:``; a run of the file is configured
with its defaults, the file's values laid over. A wrong value is refused when
the run is configured, naming the file and the option as
``params.<section>.<option>``. The run records what it runs with.
"""

import json

import pytest
from pydantic import ValidationError

from adapt.configuration.pipeline_file import PipelineFile
from adapt.configuration.pipeline_run import configured_modules, run_config
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.internal import InternalConfig
from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.persistence.store import init_store
from tests.helpers.pipeline_files import MODULES

pytestmark = pytest.mark.unit

_REALTIME = {"source": "aws_nexrad", "radar": "KBOX", "mode": "realtime"}
_HISTORICAL = {**_REALTIME, "mode": "historical"}
_WINDOW = {"start_time": "2025-06-17T18:00:00Z", "end_time": "2025-06-17T19:00:00Z"}


@pytest.fixture
def path(tmp_path):
    """A pipeline file's place: in a store root."""
    return init_store(tmp_path / "store") / "case.yaml"


def _pipeline(run=None, modules=MODULES, **params) -> PipelineFile:
    return PipelineFile.model_validate(
        {"run": run or _REALTIME, "modules": list(modules), "params": params}
    )


def _refusal(path, pipeline: PipelineFile) -> list[str]:
    """The lines of the refusal of a run of ``pipeline``."""
    with pytest.raises(ConfigError) as exc:
        run_config(pipeline, path)
    return str(exc.value).splitlines()


class TestDefaults:
    def test_a_file_that_sets_no_global_option_runs_with_these(self, path):
        assert run_config(_pipeline(), path).global_.model_dump() == {
            "z_level": 2000.0,
            "max_scan_gap_minutes": 10.0,
            "tracking_field": "reflectivity",
        }

    def test_a_file_that_sets_no_downloader_option_runs_with_these(self, path):
        assert run_config(_pipeline(), path).downloader.model_dump() == {
            "mode": "realtime",
            "radar": "KBOX",
            "latest_files": 5,
            "latest_minutes": 60,
            "poll_interval_sec": 300,
            "max_fetch_retries": 3,
            "start_time": None,
            "end_time": None,
            "min_file_size": 1024,
            "max_queue_size": 100,
            "queue_resume_fraction": 0.1,
        }

    def test_a_file_that_sets_no_logging_option_runs_with_these(self, path):
        assert run_config(_pipeline(), path).logging.model_dump() == {
            "level": "INFO",
            "console_level": "WARNING",
            "console_logs": True,
            "json_logs": False,
            "enabled": True,
            "traces": True,
            "metrics": True,
        }


class TestOptionsGiven:
    def test_every_global_option_of_the_file_reaches_the_run(self, path):
        given = {"z_level": 3000.0, "max_scan_gap_minutes": 12.5, "tracking_field": "velocity"}
        analysis = {"radar_variables": ["velocity"]}  # analysis measures the tracking field

        config = run_config(_pipeline(**{"global": given}, analysis=analysis), path)

        assert config.global_.model_dump() == given

    def test_every_downloader_option_of_the_file_reaches_the_run(self, path):
        given = {
            "latest_files": 2,
            "latest_minutes": 30,
            "poll_interval_sec": 60,
            "max_fetch_retries": 5,
            "min_file_size": 2048,
            "max_queue_size": 7,
            "queue_resume_fraction": 0.5,
        }

        downloader = run_config(_pipeline(downloader=given), path).downloader.model_dump()

        assert {key: downloader[key] for key in given} == given

    def test_every_logging_option_of_the_file_reaches_the_run(self, path):
        given = {
            "level": "DEBUG",
            "console_level": "ERROR",
            "console_logs": False,
            "json_logs": True,
            "enabled": False,
            "traces": False,
            "metrics": False,
        }

        assert run_config(_pipeline(logging=given), path).logging.model_dump() == given

    def test_an_integer_altitude_is_a_float_in_the_run(self, path):
        z_level = run_config(_pipeline(**{"global": {"z_level": 1500}}), path).global_.z_level

        assert (type(z_level), z_level) == (float, 1500.0)

    def test_a_name_given_with_spaces_around_it_is_trimmed(self, path):
        given = {"global": {"tracking_field": "  reflectivity "}}

        assert run_config(_pipeline(**given), path).global_.tracking_field == "reflectivity"


class TestRefusals:
    @pytest.mark.parametrize(
        ("section", "option", "value", "bound"),
        [
            ("global", "max_scan_gap_minutes", 0, "greater than 0"),
            ("downloader", "latest_files", 0, "greater than or equal to 1"),
            ("downloader", "latest_minutes", 0, "greater than or equal to 1"),
            ("downloader", "poll_interval_sec", 0, "greater than or equal to 1"),
            ("downloader", "max_fetch_retries", 0, "greater than or equal to 1"),
            ("downloader", "min_file_size", 0, "greater than or equal to 1"),
            ("downloader", "max_queue_size", 0, "greater than or equal to 1"),
            ("downloader", "queue_resume_fraction", 0, "greater than 0"),
            ("downloader", "queue_resume_fraction", 1, "less than 1"),
        ],
    )
    def test_a_value_outside_its_bound_is_refused_naming_the_file_and_the_option(
        self, path, section, option, value, bound
    ):
        lines = _refusal(path, _pipeline(**{section: {option: value}}))

        assert lines == [
            f"Invalid configuration (pipeline file {path}):",
            f"  • params.{section}.{option} — value {value} is out of range — "
            f"Input should be {bound}",
        ]

    @pytest.mark.parametrize(
        ("section", "option"),
        [
            ("global", "bogus"),
            ("global", "coord_names"),
            ("downloader", "output_dir"),
            ("logging", "progress_every"),
        ],
    )
    def test_an_option_the_section_does_not_have_is_refused(self, path, section, option):
        lines = _refusal(path, _pipeline(**{section: {option: 1}}))

        assert lines == [
            f"Invalid configuration (pipeline file {path}):",
            f"  • params.{section}.{option} — unrecognized option (value: 1) — not a valid "
            f"'{section}' setting; remove it or check for a typo or a renamed field",
        ]

    @pytest.mark.parametrize("option", ["level", "console_level"])
    def test_a_log_level_that_is_not_one_of_the_five_is_refused(self, path, option):
        lines = _refusal(path, _pipeline(logging={option: "LOUD"}))

        assert lines[1] == (
            f"  • params.logging.{option} — Input should be 'DEBUG', 'INFO', 'WARNING', "
            "'ERROR' or 'CRITICAL' (value: 'LOUD')"
        )

    def test_an_altitude_left_empty_is_refused_as_that_option(self, path):
        lines = _refusal(path, _pipeline(**{"global": {"z_level": None}}))

        assert lines[1].startswith("  • params.global.z_level — ")

    @pytest.mark.parametrize(
        ("option", "value"),
        [
            ("z_level", float("inf")),
            ("z_level", float("-inf")),
            ("z_level", float("nan")),
            ("max_scan_gap_minutes", float("inf")),
            ("max_scan_gap_minutes", float("nan")),
        ],
    )
    def test_a_number_that_is_not_finite_is_refused_naming_the_file_and_the_option(
        self, path, option, value
    ):
        """The run record (JSON) holds no infinity or NaN: the run could not be resumed."""
        lines = _refusal(path, _pipeline(**{"global": {option: value}}))

        assert lines == [
            f"Invalid configuration (pipeline file {path}):",
            f"  • params.global.{option} — Input should be a finite number (value: {value!r})",
        ]

    def test_the_wrong_options_of_several_sections_are_told_together(self, path):
        pipeline = _pipeline(
            **{"global": {"max_scan_gap_minutes": 0}},
            downloader={"latest_files": 0},
            logging={"level": "LOUD"},
        )

        told = [line.split(" — ")[0] for line in _refusal(path, pipeline)[1:]]

        assert told == [
            "  • params.downloader.latest_files",
            "  • params.global.max_scan_gap_minutes",
            "  • params.logging.level",
        ]

    def test_adapt_graph_refuses_the_same_value_the_same_way(self, path):
        pipeline = _pipeline(downloader={"latest_files": 0})

        with pytest.raises(ConfigError) as exc:
            configured_modules(pipeline, path)

        assert str(exc.value).splitlines() == _refusal(path, pipeline)


class TestHistoricalWindow:
    def test_a_historical_run_without_a_start_time_is_refused(self, path):
        run = {**_HISTORICAL, "end_time": _WINDOW["end_time"]}

        assert _refusal(path, _pipeline(run)) == [
            "start_time is required when mode='historical'. "
            "Set run.start_time in the pipeline file."
        ]

    def test_a_historical_run_without_an_end_time_is_refused(self, path):
        run = {**_HISTORICAL, "start_time": _WINDOW["start_time"]}

        assert _refusal(path, _pipeline(run)) == [
            "end_time is required when mode='historical'. Set run.end_time in the pipeline file."
        ]

    def test_a_file_over_a_stored_run_needs_no_window(self, path):
        run = {"source": "stored_run", "radar": "KBOX", "mode": "historical"}

        wired = configured_modules(_pipeline(run, modules=["target_selection"]), path)

        assert [module.name for module in wired] == ["target_selection"]


class TestRecord:
    def test_the_record_holds_no_setting_that_nothing_reads(self, path):
        recorded = json.loads(run_config(_pipeline(), path).model_dump_json())

        assert "output_dir" not in recorded["downloader"]
        assert "progress_every" not in recorded["logging"]

    def test_a_config_read_back_from_its_record_is_the_same_config(self, path):
        pipeline = _pipeline(
            {**_HISTORICAL, **_WINDOW},
            downloader={"max_queue_size": 7},
            logging={"json_logs": True},
        )
        config = run_config(pipeline, path)

        assert InternalConfig.model_validate_json(config.model_dump_json()) == config

    def test_a_run_config_without_its_module_list_is_not_a_run_config(self, path):
        recorded = json.loads(run_config(_pipeline(), path).model_dump_json())
        del recorded["modules"]

        with pytest.raises(ValidationError, match="modules"):
            InternalConfig.model_validate(recorded)

    @pytest.mark.parametrize("field", ["source", "run_id", "module_params", "pipeline"])
    def test_a_run_config_without_this_is_not_a_run_config(self, path, field):
        recorded = json.loads(run_config(_pipeline(), path).model_dump_json())
        del recorded[field]

        with pytest.raises(ValidationError, match=field):
            InternalConfig.model_validate(recorded)

    @pytest.mark.parametrize(
        ("section", "option"),
        [("global_", "z_level"), ("downloader", "latest_files"), ("logging", "level")],
    )
    def test_a_section_of_a_run_config_cannot_be_changed(self, path, section, option):
        settings = getattr(run_config(_pipeline(), path), section)

        with pytest.raises(ValidationError, match="frozen"):
            setattr(settings, option, getattr(settings, option))

    def test_a_listed_module_the_record_holds_no_options_for_is_refused(self, path):
        config = run_config(_pipeline(), path)
        recorded = json.loads(config.model_dump_json())
        del recorded["module_params"]["tracking"]
        incomplete = InternalConfig.model_validate(recorded)

        with pytest.raises(ConfigError) as exc:
            resolve_module_configs(incomplete, ["tracking"])

        message = str(exc.value)
        assert f"module_params of run {config.run_id}" in message
        assert "'tracking'" in message
