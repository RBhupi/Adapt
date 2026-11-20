# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tracking's options: a pipeline file sets them under ``params.tracking``.

Every option reaches the module with the value the file gives it, a value
outside an option's bounds is refused as ``params.tracking.<option>``, and the
section that held these options before (``params.tracker``) is an unknown key.
``OPTIONS`` gives every option one valid value other than its default.
"""

from typing import Any

import pytest

from adapt.configuration.pipeline_file import PipelineFile
from adapt.configuration.pipeline_run import run_config
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.modules.tracking.config import TrackingOptions
from adapt.persistence.store import init_store
from adapt.persistence.store_registry import StoreRegistry
from tests.helpers.run_configs import record_run

pytestmark = pytest.mark.unit

_MODULES = ("ingest", "detection", "projection", "analysis", "tracking")
_RUN_ID = "2026MAR23-0206-KBOX"

# Option -> a valid value that is not its default.
OPTIONS: dict[str, Any] = {
    "cell_threshold": 25.5,
    "polarity": -1,
    "growth_max_km": 3.5,
    "growth_size_km": 9.5,
    "max_overlap_mismatch": 0.45,
    "max_link_cost": 2.5,
    "residual_weight": 0.5,
    "heading_weight": 0.75,
    "max_speed_ms": 30.5,
    "max_acceleration_ms2": 0.08,
    "split_overlap_threshold": 0.5,
    "merge_overlap_threshold": 0.55,
    "latent_scans": 4,
    "identity_intensity_weight": 0.5,
    "identity_score_margin": 0.2,
    "core_field_threshold": 45.5,
    "uid_width": 12,
}

# The options that hold a real number: a file may give each a whole number.
REAL_OPTIONS = {
    "cell_threshold": 25,
    "growth_max_km": 3,
    "growth_size_km": 9,
    "max_overlap_mismatch": 1,
    "max_link_cost": 2,
    "residual_weight": 1,
    "heading_weight": 2,
    "max_speed_ms": 30,
    "max_acceleration_ms2": 1,
    "split_overlap_threshold": 1,
    "merge_overlap_threshold": 1,
    "identity_intensity_weight": 2,
    "identity_score_margin": 1,
    "core_field_threshold": 45,
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
    """The config tracking gets from a new run of a file with these ``params``."""
    config = run_config(_pipeline(params), path)
    return resolve_module_configs(config, ["tracking"])["tracking_config"]


def _refusal(path, **params) -> str:
    with pytest.raises(ConfigError) as exc:
        run_config(_pipeline(params), path)
    return str(exc.value)


def _exact(value: Any) -> Any:
    """``value`` in a form whose equality also tells number types apart."""
    return (type(value).__name__, value)


class TestEveryOptionReachesTheModule:
    def test_the_table_names_every_option_of_the_module(self):
        assert set(TrackingOptions.model_fields) == set(OPTIONS)

    @pytest.mark.parametrize("option", list(OPTIONS))
    def test_the_tables_value_is_not_the_options_default(self, option):
        assert TrackingOptions.model_fields[option].default != OPTIONS[option]

    @pytest.mark.parametrize("option", list(OPTIONS))
    def test_an_option_set_under_params_tracking_changes_that_value_only(self, path, option):
        default = _config(path)

        config = _config(path, tracking={option: OPTIONS[option]})

        assert _exact(getattr(config, option)) == _exact(OPTIONS[option])
        assert config == default.model_copy(update={option: OPTIONS[option]})

    def test_every_option_set_together_reaches_the_module(self, path):
        config = _config(path, tracking=OPTIONS)

        assert {option: _exact(getattr(config, option)) for option in OPTIONS} == {
            option: _exact(value) for option, value in OPTIONS.items()
        }

    @pytest.mark.parametrize("option", list(REAL_OPTIONS))
    def test_a_whole_number_given_for_a_real_option_reaches_the_module_as_a_float(
        self, path, option
    ):
        config = _config(path, tracking={option: REAL_OPTIONS[option]})

        assert _exact(getattr(config, option)) == _exact(float(REAL_OPTIONS[option]))

    def test_latency_may_be_switched_off(self, path):
        assert _config(path, tracking={"latent_scans": 0}).latent_scans == 0

    def test_tracking_runs_on_the_field_and_the_scan_gap_the_run_shares(self, path):
        shared = {
            "tracking_field": "velocity",
            "max_scan_gap_minutes": 4,
        }

        config = _config(path, **{"global": shared})

        assert config.field_var == "velocity"
        assert _exact(config.max_tracking_gap_minutes) == _exact(4.0)


class TestRefusals:
    @pytest.mark.parametrize(
        ("option", "value", "bound"),
        [
            ("growth_max_km", -0.5, "greater than or equal to 0"),
            ("growth_size_km", 0, "greater than 0"),
            ("max_overlap_mismatch", 0, "greater than 0"),
            ("max_overlap_mismatch", 1.5, "less than or equal to 1"),
            ("max_link_cost", 0, "greater than 0"),
            ("residual_weight", -0.5, "greater than or equal to 0"),
            ("heading_weight", -0.5, "greater than or equal to 0"),
            ("max_speed_ms", 0, "greater than 0"),
            ("max_acceleration_ms2", -0.5, "greater than or equal to 0"),
            ("split_overlap_threshold", 0, "greater than 0"),
            ("split_overlap_threshold", 1.5, "less than or equal to 1"),
            ("merge_overlap_threshold", 0, "greater than 0"),
            ("merge_overlap_threshold", 1.5, "less than or equal to 1"),
            ("latent_scans", -1, "greater than or equal to 0"),
            ("identity_intensity_weight", -0.5, "greater than or equal to 0"),
            ("identity_score_margin", -0.5, "greater than or equal to 0"),
            ("uid_width", 0, "greater than or equal to 1"),
        ],
    )
    def test_a_value_outside_an_options_bounds_is_refused(self, path, option, value, bound):
        message = _refusal(path, tracking={option: value})

        assert message.splitlines()[0] == f"Invalid configuration (pipeline file {path}):"
        assert (
            f"  • params.tracking.{option} — value {value} is out of range — "
            f"Input should be {bound}"
        ) in message.splitlines()

    def test_every_option_with_a_bound_is_refused_outside_it(self):
        """The cases above leave out no bounded option."""
        bounded = {name for name, field in TrackingOptions.model_fields.items() if field.metadata}

        assert bounded == set(OPTIONS) - {"cell_threshold", "polarity", "core_field_threshold"}

    @pytest.mark.parametrize("value", [0, 2, -2, "-1"])
    def test_a_polarity_that_is_not_a_sign_is_refused(self, path, value):
        message = _refusal(path, tracking={"polarity": value})

        (line,) = [line for line in message.splitlines() if line.startswith("  • ")]
        assert line.startswith("  • params.tracking.polarity — Input should be 1 or -1")

    @pytest.mark.parametrize(
        ("options", "option"),
        [
            ({"field_var": "velocity"}, "field_var"),
            ({"max_tracking_gap_minutes": 5}, "max_tracking_gap_minutes"),
            ({"split_overlap": 0.5}, "split_overlap"),
            ({"merge_overlap": 0.5}, "merge_overlap"),
            ({"cell_uid": {"width": 12}}, "cell_uid"),
            ({"minimum_candidate_overlap": 0.2}, "minimum_candidate_overlap"),
        ],
    )
    def test_an_option_tracking_does_not_have_is_refused(self, path, options, option):
        message = _refusal(path, tracking=options)

        assert f"  • params.tracking.{option} — unrecognized option" in message
        assert message.splitlines()[-1] == "The options of tracking: adapt config --params tracking"

    def test_the_section_that_held_these_options_is_an_unknown_key(self):
        with pytest.raises(ValueError, match="unknown params key 'tracker'"):
            _pipeline({"tracker": {"max_speed_ms": 30.0}})

    def test_the_unknown_key_message_offers_tracking(self):
        with pytest.raises(ValueError) as exc:
            _pipeline({"tracker": {}})

        sections, modules = str(exc.value).split("the sections ")[1].split("; the modules ")
        assert "tracker" not in sections.split(", ")
        assert "tracking" in modules.split(", ")


class TestRunRecord:
    def test_a_run_records_every_option_tracking_runs_with(self, path):
        given = {"max_speed_ms": 30, "uid_width": 12}

        recorded = run_config(_pipeline({"tracking": given}), path).module_params["tracking"]

        defaults = TrackingOptions().model_dump(mode="json")
        assert recorded == {**defaults, "max_speed_ms": 30.0, "uid_width": 12}
        assert list(recorded) == list(TrackingOptions.model_fields)

    @pytest.mark.parametrize("given", [{}, OPTIONS])
    def test_a_run_read_back_from_its_record_configures_tracking_the_same(self, path, given):
        config = run_config(_pipeline({"tracking": given}), path)

        stored = type(config).model_validate_json(config.model_dump_json())  # runs.config_json

        assert stored.module_params["tracking"] == config.module_params["tracking"]
        assert resolve_module_configs(stored, ["tracking"]) == resolve_module_configs(
            config, ["tracking"]
        )

    def test_a_pass_over_a_stored_run_with_other_tracking_options_is_refused(self, path):
        given = {"max_speed_ms": 30.0, "latent_scans": 4}
        recorded = run_config(_pipeline({"tracking": given}), path, run_id=_RUN_ID)
        record_run(path.parent, recorded)
        StoreRegistry.get_instance(path.parent).finalize_run(
            _RUN_ID, "completed", scans_processed=0
        )
        later = _pipeline({}, run={"source": "stored_run", "radar": "KBOX", "mode": "historical"})

        with pytest.raises(ConfigError) as exc:
            run_config(later, path, run_id=_RUN_ID)

        lines = str(exc.value).splitlines()
        assert "  • params.tracking.max_speed_ms — recorded: 30.0; this file: 40.0" in lines
        assert "  • params.tracking.latent_scans — recorded: 4; this file: 2" in lines
