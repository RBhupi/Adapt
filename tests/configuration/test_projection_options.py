# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Projection's options: a pipeline file sets them under ``params.projection``.

Every option reaches the module with the value the file gives it, a value
outside an option's bounds is refused as ``params.projection.<option>``, and the
section that held these options before (``params.projector``) is an unknown
key. ``OPTIONS`` and ``FLOW_PARAMS`` give every option one valid value other
than its default; ``method`` has a single choice, so it has no such value.
"""

from typing import Any

import pytest
from pydantic import BaseModel

from adapt.configuration.pipeline_file import PipelineFile
from adapt.configuration.pipeline_run import run_config
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.modules.projection.config import ProjectionOptions
from adapt.persistence.store import init_store
from adapt.persistence.store_registry import RunStart, StoreRegistry

pytestmark = pytest.mark.unit

_MODULES = ("ingest", "detection", "projection", "analysis", "tracking")
_RUN_ID = "2026MAR23-0206-KBOX"

# Option -> a valid value that is not its default.
OPTIONS: dict[str, Any] = {
    "max_projection_steps": 7,
    "nan_fill_value": -5.5,
    "min_motion_threshold": 0.25,
    "max_flow_magnitude": 12.5,
    "registration_step_minutes": 2,
    "projection_horizon_minutes": 30,
}

# Parameter of the ``flow_params`` part -> a valid value that is not its default.
FLOW_PARAMS: dict[str, Any] = {
    "pyr_scale": 0.8,
    "levels": 5,
    "winsize": 21,
    "iterations": 6,
    "poly_n": 5,
    "poly_sigma": 1.1,
    "flags": 256,
}


@pytest.fixture
def path(tmp_path):
    """A pipeline file's place in an initialized store."""
    return init_store(tmp_path / "store") / "case.yaml"


def _pipeline(params: dict, **sections) -> PipelineFile:
    return PipelineFile.model_validate(
        {
            "run": {"source": "aws_nexrad", "radar": "KBOX", "mode": "realtime"},
            "modules": list(_MODULES),
            "params": params,
            **sections,
        }
    )


def _config(path, **params):
    """The config projection gets from a new run of a file with these ``params``."""
    config = run_config(_pipeline(params), path)
    return resolve_module_configs(config, ["projection"])["projection_config"]


def _refusal(path, **params) -> str:
    with pytest.raises(ConfigError) as exc:
        run_config(_pipeline(params), path)
    return str(exc.value)


def _exact(value: Any) -> Any:
    """``value`` in a form whose equality also tells number types apart."""
    return (type(value).__name__, value)


class TestEveryOptionReachesTheModule:
    def test_the_tables_name_every_option_of_the_module(self):
        flow_params = ProjectionOptions.model_fields["flow_params"].annotation

        assert set(ProjectionOptions.model_fields) == set(OPTIONS) | {"method", "flow_params"}
        assert issubclass(flow_params, BaseModel)
        assert set(flow_params.model_fields) == set(FLOW_PARAMS)

    @pytest.mark.parametrize("option", list(OPTIONS))
    def test_an_option_set_under_params_projection_changes_that_value_only(self, path, option):
        default = _config(path)

        config = _config(path, projection={option: OPTIONS[option]})

        assert _exact(getattr(config, option)) == _exact(OPTIONS[option])
        assert config == default.model_copy(update={option: OPTIONS[option]})

    @pytest.mark.parametrize("param", list(FLOW_PARAMS))
    def test_a_flow_parameter_given_alone_changes_that_parameter_only(self, path, param):
        default = _config(path)

        config = _config(path, projection={"flow_params": {param: FLOW_PARAMS[param]}})

        assert _exact(getattr(config.flow_params, param)) == _exact(FLOW_PARAMS[param])
        assert config.flow_params.model_dump() == {
            **default.flow_params.model_dump(),
            param: FLOW_PARAMS[param],
        }
        assert config.model_dump(exclude={"flow_params"}) == default.model_dump(
            exclude={"flow_params"}
        )

    def test_the_one_method_may_be_named(self, path):
        assert _config(path, projection={"method": "adapt_default"}) == _config(path)

    @pytest.mark.parametrize(
        ("options", "held"),
        [
            ({"nan_fill_value": -5}, lambda config: config.nan_fill_value),
            ({"min_motion_threshold": 2}, lambda config: config.min_motion_threshold),
            ({"max_flow_magnitude": 12}, lambda config: config.max_flow_magnitude),
            ({"flow_params": {"pyr_scale": 1}}, lambda config: config.flow_params.pyr_scale),
            ({"flow_params": {"poly_sigma": 2}}, lambda config: config.flow_params.poly_sigma),
        ],
    )
    def test_a_whole_number_given_for_a_real_option_reaches_the_module_as_a_float(
        self, path, options, held
    ):
        assert isinstance(held(_config(path, projection=options)), float)

    def test_projection_runs_on_the_field_and_the_scan_gap_the_run_shares(self, path):
        shared = {"tracking_field": "velocity", "max_scan_gap_minutes": 4}

        config = _config(path, **{"global": shared})

        assert config.reflectivity_var == "velocity"
        assert _exact(config.max_time_interval_minutes) == _exact(4.0)


class TestRefusals:
    @pytest.mark.parametrize(
        ("option", "value", "bound"),
        [
            ("max_projection_steps", 0, "greater than or equal to 1"),
            ("max_projection_steps", 11, "less than or equal to 10"),
            ("min_motion_threshold", -0.5, "greater than or equal to 0"),
            ("max_flow_magnitude", 0, "greater than 0"),
            ("registration_step_minutes", 0, "greater than or equal to 1"),
            ("projection_horizon_minutes", 0, "greater than or equal to 1"),
            ("projection_horizon_minutes", 61, "less than or equal to 60"),
        ],
    )
    def test_a_value_outside_an_options_bounds_is_refused(self, path, option, value, bound):
        message = _refusal(path, projection={option: value})

        assert message.splitlines()[0] == f"Invalid configuration (pipeline file {path}):"
        assert (
            f"  • params.projection.{option} — value {value} is out of range — "
            f"Input should be {bound}"
        ) in message.splitlines()

    @pytest.mark.parametrize(
        ("param", "value", "bound"),
        [
            ("pyr_scale", 0, "greater than 0"),
            ("pyr_scale", 1.5, "less than or equal to 1"),
            ("levels", 0, "greater than or equal to 1"),
            ("winsize", 0, "greater than or equal to 1"),
            ("iterations", 0, "greater than or equal to 1"),
            ("poly_n", 4, "greater than or equal to 5"),
            ("poly_sigma", 0, "greater than 0"),
        ],
    )
    def test_a_flow_parameter_outside_its_bounds_is_refused(self, path, param, value, bound):
        message = _refusal(path, projection={"flow_params": {param: value}})

        assert (
            f"  • params.projection.flow_params.{param} — value {value} is out of range — "
            f"Input should be {bound}"
        ) in message.splitlines()

    def test_a_method_projection_does_not_have_is_refused(self, path):
        message = _refusal(path, projection={"method": "semi_lagrangian"})

        (line,) = [line for line in message.splitlines() if line.startswith("  • ")]
        assert line.startswith("  • params.projection.method — Input should be 'adapt_default'")

    @pytest.mark.parametrize(
        ("options", "option"),
        [
            ({"max_time_interval_minutes": 5}, "max_time_interval_minutes"),
            ({"reflectivity_var": "velocity"}, "reflectivity_var"),
            ({"winsize": 15}, "winsize"),
            ({"flow_params": {"window": 15}}, "flow_params.window"),
        ],
    )
    def test_an_option_projection_does_not_have_is_refused(self, path, options, option):
        message = _refusal(path, projection=options)

        assert f"  • params.projection.{option} — unrecognized option" in message
        assert (
            message.splitlines()[-1] == "The options of projection: adapt config --show projection"
        )

    def test_the_section_that_held_these_options_is_an_unknown_key(self):
        with pytest.raises(ValueError, match="unknown params key 'projector'"):
            _pipeline({"projector": {"max_projection_steps": 7}})

    def test_the_unknown_key_message_offers_projection(self):
        with pytest.raises(ValueError) as exc:
            _pipeline({"projector": {}})

        sections, modules = str(exc.value).split("the sections ")[1].split("; the modules ")
        assert "projector" not in sections.split(", ")
        assert "projection" in modules.split(", ")

    def test_options_of_projection_are_refused_when_the_file_does_not_list_it(self):
        with pytest.raises(ValueError, match="params.projection sets the options of projection"):
            _pipeline({"projection": {"max_projection_steps": 7}}, modules=["ingest"])


class TestRunRecord:
    def test_a_run_records_every_option_projection_runs_with(self, path):
        given = {"max_projection_steps": 7, "flow_params": {"winsize": 21}}

        recorded = run_config(_pipeline({"projection": given}), path).module_params["projection"]

        defaults = ProjectionOptions().model_dump(mode="json")
        assert recorded == {
            **defaults,
            "max_projection_steps": 7,
            "flow_params": {**defaults["flow_params"], "winsize": 21},
        }
        assert set(recorded) == set(ProjectionOptions.model_fields)
        assert list(recorded["flow_params"]) == list(FLOW_PARAMS)

    @pytest.mark.parametrize(
        "given",
        [{}, {"projection_horizon_minutes": 30, "flow_params": {"poly_n": 5, "levels": 5}}],
    )
    def test_a_run_read_back_from_its_record_configures_projection_the_same(self, path, given):
        config = run_config(_pipeline({"projection": given}), path)

        stored = type(config).model_validate_json(config.model_dump_json())  # runs.config_json

        assert stored.module_params["projection"] == config.module_params["projection"]
        assert resolve_module_configs(stored, ["projection"]) == resolve_module_configs(
            config, ["projection"]
        )

    def test_a_pass_over_a_stored_run_with_other_projection_options_is_refused(self, path):
        given = {"max_projection_steps": 7, "flow_params": {"winsize": 21}}
        recorded = run_config(_pipeline({"projection": given}), path, run_id=_RUN_ID)
        registry = StoreRegistry.get_instance(path.parent)
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
        registry.finalize_run(_RUN_ID, "completed", scans_processed=0)
        later = _pipeline({}, run={"source": "stored_run", "radar": "KBOX", "mode": "historical"})

        with pytest.raises(ConfigError) as exc:
            run_config(later, path, run_id=_RUN_ID)

        lines = str(exc.value).splitlines()
        assert "  • params.projection.max_projection_steps — recorded: 7; this file: 3" in lines
        assert "  • params.projection.flow_params.winsize — recorded: 21; this file: 10" in lines
