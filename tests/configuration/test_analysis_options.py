# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Analysis's options: a pipeline file sets them under ``params.analysis``.

Every option reaches the module with the value the file gives it, a value
outside an option's bounds is refused as ``params.analysis.<option>``, and the
section that held these options before (``params.analyzer``) is an unknown key.
``OPTIONS`` gives every option one valid value other than its default, and
``REACHES`` the value the module then computes with.
"""

from typing import Any

import pytest

from adapt.configuration.pipeline_file import PipelineFile
from adapt.configuration.pipeline_run import run_config
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.modules.analysis.config import ALWAYS_EXCLUDED, AnalysisOptions
from adapt.persistence.store import init_store
from adapt.persistence.store_registry import StoreRegistry
from tests.helpers.run_configs import record_run

pytestmark = pytest.mark.unit

_MODULES = ("ingest", "detection", "projection", "analysis", "tracking")
_RUN_ID = "2026MAR23-0206-KBOX"

# The variables analysis leaves out whatever the file says, in this order.
_NEVER_ANALYSED = (
    "ROI",
    "labels",
    "cell_labels",
    "cell_projections",
    "clutter_filter_power_removed",
)
_DEFAULT_VARIABLES = (
    "reflectivity",
    "velocity",
    "differential_phase",
    "differential_reflectivity",
    "spectrum_width",
    "cross_correlation_ratio",
)

# Option -> a valid value that is not its default.
OPTIONS: dict[str, Any] = {
    "radar_variables": ["velocity", "reflectivity"],
    "exclude_fields": ["velocity", "spectrum_width"],
    "adjacency_min_touching_boundary_pixels": 3,
}

# Option -> what the module computes with for the value above.
REACHES: dict[str, Any] = {
    "radar_variables": ("velocity", "reflectivity"),
    "exclude_fields": (*_NEVER_ANALYSED, "velocity", "spectrum_width"),
    "adjacency_min_touching_boundary_pixels": 3,
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
    """The config analysis gets from a new run of a file with these ``params``."""
    config = run_config(_pipeline(params), path)
    return resolve_module_configs(config, ["analysis"])["analysis_config"]


def _refusal(path, **params) -> str:
    with pytest.raises(ConfigError) as exc:
        run_config(_pipeline(params), path)
    return str(exc.value)


def _exact(value: Any) -> Any:
    """``value`` in a form whose equality also tells number types apart."""
    return (type(value).__name__, value)


class TestEveryOptionReachesTheModule:
    def test_the_table_names_every_option_of_the_module(self):
        assert set(AnalysisOptions.model_fields) == set(OPTIONS) == set(REACHES)

    @pytest.mark.parametrize("option", list(OPTIONS))
    def test_the_tables_value_is_not_the_options_default(self, option):
        assert AnalysisOptions(**{option: OPTIONS[option]}) != AnalysisOptions()

    @pytest.mark.parametrize("option", list(OPTIONS))
    def test_an_option_set_under_params_analysis_changes_that_value_only(self, path, option):
        default = _config(path)

        config = _config(path, analysis={option: OPTIONS[option]})

        assert _exact(getattr(config, option)) == _exact(REACHES[option])
        assert config == default.model_copy(update={option: REACHES[option]})

    def test_every_option_set_together_reaches_the_module(self, path):
        config = _config(path, analysis=OPTIONS)

        assert {option: _exact(getattr(config, option)) for option in OPTIONS} == {
            option: _exact(value) for option, value in REACHES.items()
        }

    def test_with_no_option_set_the_default_variables_get_statistics(self, path):
        assert _config(path).radar_variables == _DEFAULT_VARIABLES

    def test_a_variable_listed_twice_reaches_the_module_as_given(self, path):
        given = ["reflectivity", "velocity", "reflectivity"]

        assert _config(path, analysis={"radar_variables": given}).radar_variables == tuple(given)

    def test_a_name_given_with_spaces_around_it_reaches_the_module_without_them(self, path):
        given = {"radar_variables": ["reflectivity", " velocity "], "exclude_fields": [" ROI2\n"]}

        config = _config(path, analysis=given)

        assert config.radar_variables == ("reflectivity", "velocity")
        assert config.exclude_fields == (*_NEVER_ANALYSED, "ROI2")

    def test_the_tracked_field_listed_with_spaces_around_it_is_the_tracked_field(self, path):
        given = {"radar_variables": [" reflectivity ", "velocity "]}

        config = _config(path, analysis=given)

        assert config.radar_variables == ("reflectivity", "velocity")

    def test_a_run_records_a_name_without_the_spaces_given_around_it(self, path):
        given = {"radar_variables": [" reflectivity "], "exclude_fields": [" velocity "]}

        recorded = run_config(_pipeline({"analysis": given}), path).module_params["analysis"]

        assert recorded["radar_variables"] == ["reflectivity"]
        assert recorded["exclude_fields"] == ["velocity"]

    def test_analysis_runs_on_the_field_and_the_level_the_run_shares(self, path):
        shared = {"tracking_field": "velocity", "z_level": 3000}

        config = _config(path, **{"global": shared})

        assert config.reflectivity_var == "velocity"
        assert _exact(config.z_level) == _exact(3000.0)


class TestExcludedVariables:
    """``exclude_fields`` adds to the variables analysis never analyses."""

    def test_the_module_names_the_variables_it_never_analyses(self):
        assert ALWAYS_EXCLUDED == _NEVER_ANALYSED

    def test_the_option_is_empty_by_default(self):
        assert AnalysisOptions().exclude_fields == ()

    @pytest.mark.parametrize(
        ("given", "added"),
        [
            ([], ()),
            (["velocity"], ("velocity",)),
            (["new_field1", "new_field2"], ("new_field1", "new_field2")),
            # in the order given, and one the module already excludes is not repeated
            (["zeta", "ROI", "alpha"], ("zeta", "alpha")),
            (["zeta", "zeta", "alpha", "cell_labels", "zeta"], ("zeta", "alpha")),
            (["labels", "ROI"], ()),
            (list(_NEVER_ANALYSED), ()),
            (["reflectivity"], ("reflectivity",)),
        ],
    )
    def test_the_modules_own_come_first_then_each_addition_once_in_the_order_given(
        self, path, given, added
    ):
        config = _config(path, analysis={"exclude_fields": given})

        assert _exact(config.exclude_fields) == _exact((*_NEVER_ANALYSED, *added))

    def test_the_run_records_the_additions_not_the_modules_own(self, path):
        given = ["zeta", "ROI", "alpha"]

        recorded = run_config(_pipeline({"analysis": {"exclude_fields": given}}), path)

        assert recorded.module_params["analysis"]["exclude_fields"] == given


class TestTheTrackedFieldGetsStatistics:
    def test_a_tracking_field_without_statistics_is_refused(self, path):
        message = _refusal(path, **{"global": {"tracking_field": "pressure"}})

        assert message == (
            f"Pipeline file {path}: params.global.tracking_field 'pressure' is not in "
            f"params.analysis.radar_variables {list(_DEFAULT_VARIABLES)} — analysis would "
            "never compute the per-cell statistics tracking needs. Add it to the list."
        )

    def test_dropping_the_tracking_field_from_the_variables_is_refused(self, path):
        message = _refusal(path, analysis={"radar_variables": ["velocity"]})

        assert (
            "params.global.tracking_field 'reflectivity' is not in "
            "params.analysis.radar_variables ['velocity']"
        ) in message

    def test_no_variable_at_all_is_refused(self, path):
        message = _refusal(path, analysis={"radar_variables": []})

        assert "is not in params.analysis.radar_variables []" in message

    def test_a_tracking_field_among_the_variables_is_accepted(self, path):
        config = _config(
            path,
            analysis={"radar_variables": ["pressure"]},
            **{"global": {"tracking_field": "pressure"}},
        )

        assert config.reflectivity_var == "pressure"
        assert config.radar_variables == ("pressure",)

    def test_a_file_that_does_not_run_analysis_is_not_held_to_its_variables(self, path):
        pipeline = _pipeline({"global": {"tracking_field": "pressure"}}, modules=["ingest"])

        assert run_config(pipeline, path).global_.tracking_field == "pressure"


class TestRefusals:
    @pytest.mark.parametrize(
        ("option", "value", "bound"),
        [
            ("adjacency_min_touching_boundary_pixels", 0, "greater than or equal to 1"),
            ("adjacency_min_touching_boundary_pixels", -2, "greater than or equal to 1"),
        ],
    )
    def test_a_value_outside_an_options_bounds_is_refused(self, path, option, value, bound):
        message = _refusal(path, analysis={option: value})

        assert message.splitlines()[0] == f"Invalid configuration (pipeline file {path}):"
        assert (
            f"  • params.analysis.{option} — value {value} is out of range — "
            f"Input should be {bound}"
        ) in message.splitlines()

    def test_every_option_with_a_bound_is_refused_outside_it(self):
        """The cases above leave out no bounded option."""
        bounded = {name for name, field in AnalysisOptions.model_fields.items() if field.metadata}

        assert bounded == {"adjacency_min_touching_boundary_pixels"}

    @pytest.mark.parametrize("option", ["radar_variables", "exclude_fields"])
    @pytest.mark.parametrize("value", ["velocity", None, 5])
    def test_a_list_option_given_anything_but_a_list_is_refused(self, path, option, value):
        message = _refusal(path, analysis={option: value})

        (line,) = [line for line in message.splitlines() if line.startswith("  • ")]
        assert line == (
            f"  • params.analysis.{option} — must be a list (one '- item' line per entry)"
        )

    @pytest.mark.parametrize("option", ["radar_variables", "exclude_fields"])
    def test_a_list_entry_that_is_not_a_name_is_refused(self, path, option):
        message = _refusal(path, analysis={option: ["reflectivity", 5]})

        assert f"  • params.analysis.{option}.1 — Input should be a valid string" in message

    @pytest.mark.parametrize("value", [3.5, None, "three"])
    def test_a_pixel_count_that_is_not_a_whole_number_is_refused(self, path, value):
        message = _refusal(path, analysis={"adjacency_min_touching_boundary_pixels": value})

        (line,) = [line for line in message.splitlines() if line.startswith("  • ")]
        assert line.startswith(
            "  • params.analysis.adjacency_min_touching_boundary_pixels — Input should be a valid "
            "integer"
        )

    @pytest.mark.parametrize(
        "option",
        ["reflectivity_var", "z_level", "adjacency_min_touching", "max_projection_steps", "bogus"],
    )
    def test_an_option_analysis_does_not_have_is_refused(self, path, option):
        message = _refusal(path, analysis={option: 1})

        assert f"  • params.analysis.{option} — unrecognized option" in message
        assert message.splitlines()[-1] == "The options of analysis: adapt config --params analysis"

    def test_the_section_that_held_these_options_is_an_unknown_key(self):
        with pytest.raises(ValueError, match="unknown params key 'analyzer'"):
            _pipeline({"analyzer": {"adjacency_min_touching_boundary_pixels": 2}})

    def test_the_unknown_key_message_offers_analysis(self):
        with pytest.raises(ValueError) as exc:
            _pipeline({"analyzer": {}})

        sections, modules = str(exc.value).split("the sections ")[1].split("; the modules ")
        assert "analyzer" not in sections.split(", ")
        assert "analysis" in modules.split(", ")


class TestRunRecord:
    def test_a_run_records_every_option_analysis_runs_with(self, path):
        given = {"exclude_fields": ["velocity"], "adjacency_min_touching_boundary_pixels": 3}

        recorded = run_config(_pipeline({"analysis": given}), path).module_params["analysis"]

        assert recorded == {"radar_variables": list(_DEFAULT_VARIABLES), **given}
        assert list(recorded) == list(AnalysisOptions.model_fields)

    @pytest.mark.parametrize("given", [{}, OPTIONS])
    def test_a_run_read_back_from_its_record_configures_analysis_the_same(self, path, given):
        config = run_config(_pipeline({"analysis": given}), path)

        stored = type(config).model_validate_json(config.model_dump_json())  # runs.config_json

        assert stored.module_params["analysis"] == config.module_params["analysis"]
        assert resolve_module_configs(stored, ["analysis"]) == resolve_module_configs(
            config, ["analysis"]
        )

    def test_a_pass_over_a_stored_run_with_other_analysis_options_is_refused(self, path):
        given = {"exclude_fields": ["velocity"], "adjacency_min_touching_boundary_pixels": 3}
        recorded = run_config(_pipeline({"analysis": given}), path, run_id=_RUN_ID)
        record_run(path.parent, recorded)
        StoreRegistry.get_instance(path.parent).finalize_run(
            _RUN_ID, "completed", scans_processed=0
        )
        later = _pipeline({}, run={"source": "stored_run", "radar": "KBOX", "mode": "historical"})

        with pytest.raises(ConfigError) as exc:
            run_config(later, path, run_id=_RUN_ID)

        lines = str(exc.value).splitlines()
        assert '  • params.analysis.exclude_fields — recorded: ["velocity"]; this file: []' in lines
        assert (
            "  • params.analysis.adjacency_min_touching_boundary_pixels — recorded: 3; this file: 1"
        ) in lines
