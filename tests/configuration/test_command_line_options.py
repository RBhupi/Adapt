# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Options given on the command line (``--set params.<key>.<option>=<value>``).

They are laid over the pipeline file's ``params``: module defaults, then the
file, then the command line. The run is configured from the result exactly as
if the file had said it, and records what was given.
"""

from dataclasses import replace

import pytest
import yaml

from adapt.configuration.pipeline_file import load_pipeline_file
from adapt.configuration.pipeline_run import run_config
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.persistence.store import init_store
from adapt.persistence.store_registry import RunStart, StoreRegistry
from tests.helpers.pipeline_files import MODULES, TARGET_SELECTION, write_pipeline

pytestmark = pytest.mark.unit

_REALTIME = {"source": "aws_nexrad", "radar": "KTST", "mode": "realtime"}
_RUN_ID = "2026MAR23-0206-KTST"


@pytest.fixture
def root(tmp_path):
    return init_store(tmp_path / "store")


def _file(root, modules=MODULES, **params):
    return write_pipeline(root, "case", run=_REALTIME, modules=list(modules), params=params)


def _params(root, *overrides, **params) -> dict:
    """The params of the file with ``params``, given ``overrides`` on the command line."""
    return load_pipeline_file(_file(root, **params), overrides).params


class TestWhatIsSet:
    def test_an_option_replaces_what_the_file_says(self, root):
        params = _params(
            root,
            "params.tracking.max_speed_ms=35",
            tracking={"max_speed_ms": 30.0, "latent_scans": 3},
        )

        assert params["tracking"] == {"max_speed_ms": 35, "latent_scans": 3}

    def test_an_option_the_file_does_not_give_is_added(self, root):
        params = _params(root, "params.tracking.max_speed_ms=35", detection={"h_maxima": 4.0})

        assert params == {"detection": {"h_maxima": 4.0}, "tracking": {"max_speed_ms": 35}}

    def test_one_key_deep_inside_a_nested_part_is_set_alone(self, root):
        path = _file(root, modules=[*MODULES, "target_selection"])

        params = load_pipeline_file(
            path, ("params.target_selection.score.site_preference.projection_steps=3",)
        ).params

        assert params["target_selection"] == {"score": {"site_preference": {"projection_steps": 3}}}

    def test_a_section_is_set_like_a_module(self, root):
        assert _params(root, "params.global.z_level=3000")["global"] == {"z_level": 3000}

    @pytest.mark.parametrize(
        "written, value",
        [
            ("35", 35),
            ("3.5", 3.5),
            ("true", True),
            ("null", None),
            ("", None),
            ("[velocity, spectrum_width]", ["velocity", "spectrum_width"]),
            ("{reflectivity: DBZ}", {"reflectivity": "DBZ"}),
            ("dist_beam", "dist_beam"),
            ("'35'", "35"),
        ],
    )
    def test_a_value_is_read_as_the_file_would_read_it(self, root, written, value):
        params = _params(root, f"params.ingest.anything={written}")

        assert params["ingest"]["anything"] == value

    def test_a_value_may_hold_an_equals_sign(self, root):
        params = _params(root, "params.ingest.anything=a=b")

        assert params["ingest"]["anything"] == "a=b"

    def test_of_two_settings_of_one_option_the_later_one_holds(self, root):
        params = _params(root, "params.tracking.max_speed_ms=35", "params.tracking.max_speed_ms=45")

        assert params["tracking"]["max_speed_ms"] == 45

    def test_the_file_remembers_what_the_command_line_gave(self, root):
        given = ("params.tracking.max_speed_ms=35", "params.global.z_level=3000")

        assert load_pipeline_file(_file(root), given).overrides == given

    def test_without_the_command_line_nothing_is_set(self, root):
        pipeline = load_pipeline_file(_file(root, tracking={"max_speed_ms": 30.0}))

        assert pipeline.overrides == ()
        assert pipeline.params == {"tracking": {"max_speed_ms": 30.0}}


class TestRefusals:
    @pytest.mark.parametrize(
        "given",
        [
            "params.tracking.max_speed_ms",  # no value
            "tracking.max_speed_ms=35",  # not under params
            "run.radar=KLOT",  # the run section is the file's
            "modules=[ingest]",
            "params.tracking=35",  # names no option
            "params=1",
            "params..max_speed_ms=35",
            "=35",
        ],
    )
    def test_a_setting_that_names_no_option_under_params_is_refused(self, root, given):
        with pytest.raises(ConfigError) as refusal:
            load_pipeline_file(_file(root), (given,))

        message = str(refusal.value)
        assert f"--set {given}" in message
        assert "params.<module or section>.<option>=<value>" in message

    def test_a_key_inside_an_option_that_is_not_a_mapping_is_refused(self, root):
        path = _file(root, tracking={"max_speed_ms": 30.0})

        with pytest.raises(ConfigError, match="params.tracking.max_speed_ms is not a mapping"):
            load_pipeline_file(path, ("params.tracking.max_speed_ms.unit=ms",))

    def test_a_value_that_is_not_yaml_is_refused(self, root):
        with pytest.raises(ConfigError, match=r"--set params.ingest.fields=\[a"):
            load_pipeline_file(_file(root), ("params.ingest.fields=[a",))

    def test_options_of_a_module_the_file_does_not_run_are_refused_as_in_the_file(self, root):
        with pytest.raises(ConfigError, match="params.xlma_stat sets the options of xlma_stat"):
            load_pipeline_file(_file(root), ("params.xlma_stat.search_radius_m=900",))

    def test_a_params_key_that_is_no_module_or_section_is_refused_as_in_the_file(self, root):
        with pytest.raises(ConfigError, match="unknown params key 'traking'"):
            load_pipeline_file(_file(root), ("params.traking.max_speed_ms=35",))

    def test_a_file_without_its_params_section_is_refused_whatever_is_set(self, root):
        path = _file(root)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        del data["params"]
        path.write_text(yaml.safe_dump(data))

        with pytest.raises(ConfigError, match="params — required option is missing"):
            load_pipeline_file(path, ("params.tracking.max_speed_ms=35",))

    def test_what_a_module_refuses_names_the_command_line_too(self, root):
        """The tracked field must be one analysis measures: a rule analysis checks itself."""
        path = _file(root)
        given = ("params.analysis.radar_variables=[velocity]",)

        with pytest.raises(ConfigError) as refusal:
            run_config(load_pipeline_file(path, given), path)

        assert f"with --set {given[0]}" in str(refusal.value)

    def test_the_file_cannot_hold_what_the_command_line_gives(self, root):
        path = _file(root)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        path.write_text(yaml.safe_dump({**data, "overrides": ["params.global.z_level=1"]}))

        with pytest.raises(ConfigError, match="overrides"):
            load_pipeline_file(path)


class TestTheRun:
    def _config(self, root, *overrides, run_id=None, **params):
        path = _file(root, **params)
        return run_config(load_pipeline_file(path, overrides), path, run_id)

    def test_the_module_runs_with_the_option_as_set(self, root):
        config = self._config(root, "params.tracking.max_speed_ms=35", tracking={"latent_scans": 3})

        tracking = resolve_module_configs(config, ["tracking"])["tracking_config"]

        assert tracking.max_speed_ms == 35.0  # the command line
        assert tracking.latent_scans == 3  # the file
        assert tracking.max_link_cost == 1.8  # the module's default

    def test_the_run_records_the_option_and_that_the_command_line_gave_it(self, root):
        config = self._config(root, "params.tracking.max_speed_ms=35")

        assert config.module_params["tracking"]["max_speed_ms"] == 35.0
        assert config.pipeline.content["overrides"] == ["params.tracking.max_speed_ms=35"]
        assert config.pipeline.content["params"]["tracking"] == {"max_speed_ms": 35}

    def test_a_run_without_the_command_line_records_that_nothing_was_set(self, root):
        assert self._config(root).pipeline.content["overrides"] == []

    def test_a_wrong_value_is_refused_naming_the_command_line(self, root):
        path = _file(root)

        with pytest.raises(ConfigError) as refusal:
            run_config(load_pipeline_file(path, ("params.tracking.max_speed_ms=0",)), path)

        lines = str(refusal.value).splitlines()
        assert lines[0] == (
            f"Invalid configuration (pipeline file {path} with --set "
            "params.tracking.max_speed_ms=0):"
        )
        assert lines[1] == (
            "  • params.tracking.max_speed_ms — value 0 is out of range — "
            "Input should be greater than 0"
        )

    def test_a_wrong_section_value_is_refused_naming_the_command_line(self, root):
        path = _file(root)

        with pytest.raises(ConfigError) as refusal:
            run_config(load_pipeline_file(path, ("params.global.max_scan_gap_minutes=0",)), path)

        assert (
            str(refusal.value)
            .splitlines()[0]
            .endswith("with --set params.global.max_scan_gap_minutes=0):")
        )

    def test_a_run_that_is_resumed_keeps_its_options(self, root):
        recorded = self._config(root, run_id=_RUN_ID)
        registry = StoreRegistry.get_instance(root)
        registry.register_collection("KTST", source_kind="aws_nexrad")
        start = RunStart(
            run_id=_RUN_ID,
            collection_id="KTST",
            config_hash="h",
            config_json=recorded.model_dump_json(),
            pipeline_version="0",
            environment_json="{}",
        )
        registry.begin_run(replace(start))

        with pytest.raises(ConfigError) as refusal:
            self._config(root, "params.tracking.max_speed_ms=35", run_id=_RUN_ID)

        message = str(refusal.value)
        assert _RUN_ID in message
        assert "--set" in message

    def test_a_rule_over_a_stored_run_may_be_set_on_the_command_line(self, root):
        recorded = self._config(root, run_id=_RUN_ID)
        registry = StoreRegistry.get_instance(root)
        registry.register_collection("KTST", source_kind="aws_nexrad")
        registry.begin_run(
            RunStart(
                run_id=_RUN_ID,
                collection_id="KTST",
                config_hash="h",
                config_json=recorded.model_dump_json(),
                pipeline_version="0",
                environment_json="{}",
            )
        )
        registry.finalize_run(_RUN_ID, "completed", scans_processed=0)
        later = write_pipeline(
            root,
            "later",
            run={"source": "stored_run", "radar": "KTST", "mode": "historical"},
            modules=["target_selection"],
            params={"target_selection": TARGET_SELECTION},
        )

        config = run_config(
            load_pipeline_file(later, ("params.target_selection.switch.margin=9",)), later, _RUN_ID
        )

        assert config.module_params["target_selection"]["switch"]["margin"] == 9.0
