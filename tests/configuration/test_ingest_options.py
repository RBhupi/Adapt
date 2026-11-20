# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Ingest's options: a pipeline file sets them under ``params.ingest``.

Every option reaches the module with the value the file gives it, a value
outside an option's bounds is refused as ``params.ingest.<option>``, and the
sections that held these options before (``params.reader``,
``params.regridder``) are unknown keys. ``OPTIONS`` gives every option one
valid value other than its default, and ``REACHES`` the value the module then
computes with; ``file_format`` has a single choice, so it has no such value.
"""

from typing import Any

import pytest

from adapt.configuration.pipeline_file import PipelineFile
from adapt.configuration.pipeline_run import run_config
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.modules.ingest.config import IngestOptions
from adapt.persistence.store import init_store
from adapt.persistence.store_registry import StoreRegistry
from tests.helpers.run_configs import record_run

pytestmark = pytest.mark.unit

_MODULES = ("ingest", "detection", "projection", "analysis", "tracking")
_RUN_ID = "2026MAR23-0206-KBOX"

# Option -> a valid value that is not its default, as a pipeline file gives it.
OPTIONS: dict[str, Any] = {
    "field_map": {"corrected_reflectivity": "reflectivity", "vel": "velocity"},
    "fields": ["reflectivity", "velocity"],
    "grid_shape": [21, 101, 121],
    "grid_limits": [[0, 10000], [-50000, 50000], [-60000.5, 60000.5]],
    "roi_func": "dist",
    "min_radius": 900.5,
    "weighting_function": "barnes",
}

# Option -> what the module computes with for the value above.
REACHES: dict[str, Any] = {
    "field_map": {"corrected_reflectivity": "reflectivity", "vel": "velocity"},
    "fields": ("reflectivity", "velocity"),
    "grid_shape": (21, 101, 121),
    "grid_limits": ((0.0, 10000.0), (-50000.0, 50000.0), (-60000.5, 60000.5)),
    "roi_func": "dist",
    "min_radius": 900.5,
    "weighting_function": "barnes",
}

# The sections that held the options before, and what each held.
OLD_SECTIONS: dict[str, tuple[str, ...]] = {
    "reader": ("file_format", "field_map", "fields"),
    "regridder": ("grid_shape", "grid_limits", "roi_func", "min_radius", "weighting_function"),
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
    """The config ingest gets from a new run of a file with these ``params``."""
    config = run_config(_pipeline(params), path)
    return resolve_module_configs(config, ["ingest"])["ingest_config"]


def _refusal(path, **params) -> str:
    with pytest.raises(ConfigError) as exc:
        run_config(_pipeline(params), path)
    return str(exc.value)


def _refused_option(path, **params) -> str:
    """The one line of a refusal that names the option at fault."""
    (line,) = [line for line in _refusal(path, **params).splitlines() if line.startswith("  • ")]
    return line


def _exact(value: Any) -> Any:
    """``value`` in a form whose equality also tells number and sequence types
    apart (2000 is not 2000.0, a list is not a tuple)."""
    if isinstance(value, dict):
        return ("dict", [(key, _exact(item)) for key, item in value.items()])
    if isinstance(value, list | tuple):
        return (type(value).__name__, [_exact(item) for item in value])
    return (type(value).__name__, value)


class TestEveryOptionReachesTheModule:
    def test_the_tables_name_every_option_of_the_module(self):
        every = set(IngestOptions.model_fields)

        assert every == set(OPTIONS) | {"file_format"} == set(REACHES) | {"file_format"}
        assert every == {option for held in OLD_SECTIONS.values() for option in held}

    @pytest.mark.parametrize("option", list(OPTIONS))
    def test_the_tables_value_is_not_the_options_default(self, option):
        assert IngestOptions(**{option: OPTIONS[option]}) != IngestOptions()

    @pytest.mark.parametrize("option", list(OPTIONS))
    def test_an_option_set_under_params_ingest_changes_that_value_only(self, path, option):
        default = _config(path)

        config = _config(path, ingest={option: OPTIONS[option]})

        assert _exact(getattr(config, option)) == _exact(REACHES[option])
        assert config == default.model_copy(update={option: REACHES[option]})

    def test_every_option_set_together_reaches_the_module(self, path):
        config = _config(path, ingest=OPTIONS)

        assert {option: _exact(getattr(config, option)) for option in OPTIONS} == {
            option: _exact(value) for option, value in REACHES.items()
        }

    def test_the_one_file_format_may_be_named(self, path):
        assert _config(path, ingest={"file_format": "nexrad_archive"}) == _config(path)

    @pytest.mark.parametrize("choice", ["cressman", "barnes", "nearest"])
    def test_every_weighting_of_the_gates_may_be_chosen(self, path, choice):
        config = _config(path, ingest={"weighting_function": choice})

        assert config.weighting_function == choice

    @pytest.mark.parametrize("choice", ["dist_beam", "dist"])
    def test_every_radius_of_influence_function_may_be_chosen(self, path, choice):
        assert _config(path, ingest={"roi_func": choice}).roi_func == choice

    def test_with_no_option_set_every_field_of_the_file_is_kept_under_its_own_name(self, path):
        config = _config(path)

        assert _exact(config.field_map) == _exact({})
        assert _exact(config.fields) == _exact(())

    def test_a_whole_number_given_for_the_radius_reaches_the_module_as_a_float(self, path):
        assert _exact(_config(path, ingest={"min_radius": 2000}).min_radius) == _exact(2000.0)

    def test_a_field_listed_twice_reaches_the_module_as_given(self, path):
        given = ["reflectivity", "velocity", "reflectivity"]

        assert _config(path, ingest={"fields": given}).fields == tuple(given)

    def test_a_name_given_with_spaces_around_it_reaches_the_module_without_them(self, path):
        given = {"fields": [" reflectivity ", "velocity\n"], "field_map": {" dbz ": " velocity "}}

        config = _config(path, ingest=given)

        assert config.fields == ("reflectivity", "velocity")
        assert config.field_map == {"dbz": "velocity"}

    def test_ingest_slices_the_grid_at_the_level_the_run_shares(self, path):
        config = _config(path, **{"global": {"z_level": 3000}})

        assert _exact(config.z_level) == _exact(3000.0)


class TestTheTrackedFieldSurvivesIngest:
    """``fields`` keeps only what it lists: the field the run tracks must be on it."""

    def test_a_selection_without_the_tracking_field_is_refused(self, path):
        message = _refusal(path, ingest={"fields": ["velocity", "spectrum_width"]})

        assert message == (
            f"Pipeline file {path}: params.global.tracking_field 'reflectivity' is excluded by "
            "params.ingest.fields ['velocity', 'spectrum_width'] — the tracked field would "
            "never survive ingest. Add it to params.ingest.fields or clear the list."
        )

    def test_a_tracking_field_the_selection_lacks_is_refused(self, path):
        message = _refusal(
            path,
            ingest={"fields": ["reflectivity"]},
            analysis={"radar_variables": ["velocity"]},
            **{"global": {"tracking_field": "velocity"}},
        )

        assert (
            "params.global.tracking_field 'velocity' is excluded by "
            "params.ingest.fields ['reflectivity']"
        ) in message

    def test_a_selection_with_the_tracking_field_is_accepted(self, path):
        config = _config(
            path,
            ingest={"fields": ["velocity"]},
            analysis={"radar_variables": ["velocity"]},
            **{"global": {"tracking_field": "velocity"}},
        )

        assert config.fields == ("velocity",)

    def test_an_empty_selection_keeps_any_tracking_field(self, path):
        config = _config(
            path,
            ingest={"fields": []},
            analysis={"radar_variables": ["pressure"]},
            **{"global": {"tracking_field": "pressure"}},
        )

        assert config.fields == ()


class TestRefusals:
    @pytest.mark.parametrize(
        ("option", "value", "bound"),
        [
            ("min_radius", 0, "greater than 0"),
            ("min_radius", -5, "greater than 0"),
        ],
    )
    def test_a_value_outside_an_options_bounds_is_refused(self, path, option, value, bound):
        message = _refusal(path, ingest={option: value})

        assert message.splitlines()[0] == f"Invalid configuration (pipeline file {path}):"
        assert (
            f"  • params.ingest.{option} — value {value} is out of range — Input should be {bound}"
        ) in message.splitlines()

    def test_every_option_with_a_bound_is_refused_outside_it(self):
        """The cases above leave out no bounded option."""
        bounded = {name for name, field in IngestOptions.model_fields.items() if field.metadata}

        assert bounded == {"min_radius"}

    @pytest.mark.parametrize(
        ("option", "value", "choices"),
        [
            ("file_format", "odim_h5", "'nexrad_archive'"),
            ("roi_func", "constant", "'dist_beam' or 'dist'"),
            ("roi_func", "DIST", "'dist_beam' or 'dist'"),
            ("weighting_function", "linear", "'cressman', 'barnes' or 'nearest'"),
        ],
    )
    def test_a_choice_an_option_does_not_offer_is_refused(self, path, option, value, choices):
        line = _refused_option(path, ingest={option: value})

        assert line == f"  • params.ingest.{option} — Input should be {choices} (value: {value!r})"

    @pytest.mark.parametrize(
        ("value", "at"),
        [
            ([21, 101], "grid_shape.2"),
            ([21, 101, 121, 3], "grid_shape"),
            ([21.5, 101, 121], "grid_shape.0"),
            (["many", 101, 121], "grid_shape.0"),
            ("41,301,301", "grid_shape"),
        ],
    )
    def test_a_grid_shape_that_is_not_three_whole_numbers_is_refused(self, path, value, at):
        line = _refused_option(path, ingest={"grid_shape": value})

        assert line.startswith(f"  • params.ingest.{at} — ")

    @pytest.mark.parametrize(
        ("value", "at"),
        [
            ([[0, 10000], [-50000, 50000]], "grid_limits.2"),
            ([[0, 10000, 20000], [-50000, 50000], [-50000, 50000]], "grid_limits.0"),
            ([[0], [-50000, 50000], [-50000, 50000]], "grid_limits.0.1"),
            ([0, 10000, 20000], "grid_limits.0"),
        ],
    )
    def test_grid_limits_that_are_not_three_pairs_are_refused(self, path, value, at):
        lines = [
            line
            for line in _refusal(path, ingest={"grid_limits": value}).splitlines()
            if line.startswith("  • ")
        ]

        assert lines[0].startswith(f"  • params.ingest.{at} — ")

    @pytest.mark.parametrize("value", ["reflectivity", 5])
    def test_a_selection_that_is_not_a_list_is_refused(self, path, value):
        line = _refused_option(path, ingest={"fields": value})

        assert line == "  • params.ingest.fields — must be a list (one '- item' line per entry)"

    def test_a_selected_field_that_is_not_a_name_is_refused(self, path):
        line = _refused_option(path, ingest={"fields": ["reflectivity", 5]})

        assert line.startswith("  • params.ingest.fields.1 — Input should be a valid string")

    @pytest.mark.parametrize("value", [["corrected_reflectivity"], "reflectivity"])
    def test_renames_that_are_not_a_mapping_are_refused(self, path, value):
        line = _refused_option(path, ingest={"field_map": value})

        assert line == (
            "  • params.ingest.field_map — must be a mapping of options "
            "(key: value lines indented under it)"
        )

    def test_a_rename_to_something_that_is_not_a_name_is_refused(self, path):
        line = _refused_option(path, ingest={"field_map": {"dbz": 1}})

        assert line.startswith("  • params.ingest.field_map.dbz — Input should be a valid string")

    @pytest.mark.parametrize("option", list(IngestOptions.model_fields))
    def test_null_is_refused_for_every_option(self, path, option):
        """None of them allows it, and it is never read as "use the default"."""
        line = _refused_option(path, ingest={option: None})

        assert line.startswith(f"  • params.ingest.{option} — ")

    @pytest.mark.parametrize("option", ["z_level", "reflectivity_var", "radar", "bogus"])
    def test_an_option_ingest_does_not_have_is_refused(self, path, option):
        message = _refusal(path, ingest={option: 1})

        assert f"  • params.ingest.{option} — unrecognized option" in message
        assert message.splitlines()[-1] == "The options of ingest: adapt config --params ingest"

    @pytest.mark.parametrize("section", list(OLD_SECTIONS))
    def test_a_section_that_held_these_options_is_an_unknown_key(self, section):
        given = {option: OPTIONS[option] for option in OLD_SECTIONS[section] if option in OPTIONS}

        with pytest.raises(ValueError, match=f"unknown params key '{section}'"):
            _pipeline({section: given})

    @pytest.mark.parametrize("section", list(OLD_SECTIONS))
    def test_the_unknown_key_message_offers_ingest(self, section):
        with pytest.raises(ValueError) as exc:
            _pipeline({section: {}})

        sections, modules = str(exc.value).split("the sections ")[1].split("; the modules ")
        assert section not in sections.split(", ")
        assert "ingest" in modules.split(", ")


class TestRunRecord:
    def test_a_run_records_every_option_ingest_runs_with(self, path):
        given = {"field_map": {"dbz": "reflectivity"}, "min_radius": 2000}

        recorded = run_config(_pipeline({"ingest": given}), path).module_params["ingest"]

        assert recorded == {
            "file_format": "nexrad_archive",
            "field_map": {"dbz": "reflectivity"},
            "fields": [],
            "grid_shape": [41, 301, 301],
            "grid_limits": [[0.0, 20000.0], [-150000.0, 150000.0], [-150000.0, 150000.0]],
            "roi_func": "dist_beam",
            "min_radius": 2000.0,
            "weighting_function": "cressman",
        }
        assert list(recorded) == list(IngestOptions.model_fields)

    @pytest.mark.parametrize("given", [{}, OPTIONS])
    def test_a_run_read_back_from_its_record_configures_ingest_the_same(self, path, given):
        config = run_config(_pipeline({"ingest": given}), path)

        stored = type(config).model_validate_json(config.model_dump_json())  # runs.config_json

        assert stored.module_params["ingest"] == config.module_params["ingest"]
        assert resolve_module_configs(stored, ["ingest"]) == resolve_module_configs(
            config, ["ingest"]
        )

    def test_a_radius_json_cannot_hold_is_refused_naming_the_file(self, path):
        message = _refusal(path, ingest={"min_radius": float("inf")})

        assert message.splitlines()[0] == (
            f"Invalid configuration (pipeline file {path}, as the run records it — "
            "JSON holds no infinity or NaN):"
        )
        assert message.splitlines()[1].startswith("  • params.ingest.min_radius — ")

    def test_a_pass_over_a_stored_run_with_other_ingest_options_is_refused(self, path):
        given = {"field_map": {"dbz": "reflectivity"}, "min_radius": 2000}
        recorded = run_config(_pipeline({"ingest": given}), path, run_id=_RUN_ID)
        record_run(path.parent, recorded)
        StoreRegistry.get_instance(path.parent).finalize_run(
            _RUN_ID, "completed", scans_processed=0
        )
        later = _pipeline({}, run={"source": "stored_run", "radar": "KBOX", "mode": "historical"})

        with pytest.raises(ConfigError) as exc:
            run_config(later, path, run_id=_RUN_ID)

        lines = str(exc.value).splitlines()
        assert (
            '  • params.ingest.field_map — recorded: {"dbz": "reflectivity"}; this file: {}'
        ) in lines
        assert "  • params.ingest.min_radius — recorded: 2000.0; this file: 1750.0" in lines
