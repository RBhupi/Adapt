# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Detection's options: a pipeline file sets them under ``params.detection``.

Every option reaches the module with the value the file gives it, a value
outside an option's bounds is refused as ``params.detection.<option>``, and the
section that held these options before (``params.segmenter``) is an unknown
key. ``OPTIONS`` and ``METHOD_PARAMS`` give every option one valid value other
than its default.
"""

from typing import Any

import pytest
from pydantic import BaseModel

from adapt.configuration.pipeline_file import PipelineFile
from adapt.configuration.pipeline_run import run_config
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.modules.detection.config import DetectionOptions
from adapt.persistence.store import init_store
from adapt.persistence.store_registry import StoreRegistry
from tests.helpers.run_configs import record_run

pytestmark = pytest.mark.unit

_MODULES = ("ingest", "detection", "projection", "analysis", "tracking")
_RUN_ID = "2026MAR23-0206-KBOX"

# Option -> a valid value that is not its default.
OPTIONS: dict[str, Any] = {
    "method": "steiner_conv_strat",
    "min_cellsize_gridpoint": 12,
    "max_cellsize_gridpoint": 400,
    "closing_radius": 0,
    "filter_by_size": False,
    "h_maxima": 2.5,
    "seed_carry_frames": 3,
    "carried_exempt_size_filter": True,
    "seed_carry_min_separation": 9,
}

# Method -> parameter of its ``<method>_params`` block -> a value that is not its default.
METHOD_PARAMS: dict[str, dict[str, Any]] = {
    "threshold": {"threshold": -10.0},
    "conv_strat_raut": {
        "zr_a": 300.0,
        "zr_b": 1.4,
        "core_wt_threshold": 6.0,
        "conv_wt_threshold": 2.0,
        "conv_scale_km": 30.0,
        "min_reflectivity": 0.0,
        "conv_min_refl": 20.0,
        "conv_core_threshold": 45.0,
    },
    "conv_strat_yuter": {
        "always_core_thres": 45.0,
        "bkg_rad_km": 9.0,
        "use_cosine": False,
        "max_diff": 4.0,
        "zero_diff_cos_val": 50.0,
        "scalar_diff": 2.0,
        "use_addition": False,
        "calc_thres": 0.5,
        "weak_echo_thres": 3.0,
        "min_dBZ_used": 3.0,
        "dB_averaging": False,
        "remove_small_objects": False,
        "min_km2_size": 12.0,
        "val_for_max_conv_rad": 35.0,
        "max_conv_rad_km": 6.0,
    },
    "feature_detection": {
        "always_core_thres": 45.0,
        "bkg_rad_km": 9.0,
        "use_cosine": False,
        "max_diff": 4.0,
        "zero_diff_cos_val": 50.0,
        "scalar_diff": 2.0,
        "use_addition": False,
        "calc_thres": 0.5,
        "weak_echo_thres": 3.0,
        "min_val_used": 3.0,
        "dB_averaging": False,
        "remove_small_objects": False,
        "min_km2_size": 12.0,
        "binary_close": True,
        "val_for_max_rad": 35.0,
        "max_rad_km": 6.0,
    },
    "steiner_conv_strat": {
        "intense": 45.0,
        "peak_relation": "sgp",
        "area_relation": "large",
        "bkg_rad": 9000.0,
        "use_intense": False,
    },
}
_EACH_METHOD_PARAM = [
    (method, param, value)
    for method, block in METHOD_PARAMS.items()
    for param, value in block.items()
]


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
    """The config detection gets from a new run of a file with these ``params``."""
    config = run_config(_pipeline(params), path)
    return resolve_module_configs(config, ["detection"])["detection_config"]


def _refusal(path, **params) -> str:
    with pytest.raises(ConfigError) as exc:
        run_config(_pipeline(params), path)
    return str(exc.value)


def _exact(value: Any) -> Any:
    """``value`` in a form whose equality also tells number types apart."""
    if isinstance(value, dict):
        return [(key, _exact(item)) for key, item in value.items()]
    return (type(value).__name__, value)


class TestEveryOptionReachesTheModule:
    def test_the_tables_name_every_option_of_the_module(self):
        blocks = {f"{method}_params" for method in METHOD_PARAMS}

        assert set(DetectionOptions.model_fields) == set(OPTIONS) | blocks
        for method, params in METHOD_PARAMS.items():
            block = DetectionOptions.model_fields[f"{method}_params"].annotation
            assert issubclass(block, BaseModel)
            assert set(block.model_fields) == set(params), method

    @pytest.mark.parametrize("option", [name for name in OPTIONS if name != "method"])
    def test_an_option_set_under_params_detection_changes_that_value_only(self, path, option):
        default = _config(path)

        config = _config(path, detection={option: OPTIONS[option]})

        assert _exact(getattr(config, option)) == _exact(OPTIONS[option])
        assert config == default.model_copy(update={option: OPTIONS[option]})

    def test_the_method_set_under_params_detection_is_the_one_the_module_runs(self, path):
        config = _config(path, detection={"method": OPTIONS["method"]})

        assert config.method == "steiner_conv_strat"
        assert list(config.method_params) == list(METHOD_PARAMS["steiner_conv_strat"])

    @pytest.mark.parametrize(("method", "param", "value"), _EACH_METHOD_PARAM)
    def test_a_parameter_of_the_selected_method_changes_that_parameter_only(
        self, path, method, param, value
    ):
        default = _config(path, detection={"method": method}).method_params

        config = _config(path, detection={"method": method, f"{method}_params": {param: value}})

        assert _exact(config.method_params) == _exact({**default, param: value})

    def test_a_whole_number_given_for_a_threshold_reaches_the_module_as_a_float(self, path):
        config = _config(path, detection={"threshold_params": {"threshold": 40}})

        assert _exact(config.method_params) == _exact({"threshold": 40.0})

    def test_detection_runs_on_the_field_and_level_the_run_shares(self, path):
        shared = {"tracking_field": "velocity", "z_level": 3000}

        config = _config(path, **{"global": shared})

        assert config.reflectivity_var == "velocity"
        assert _exact(config.z_level) == _exact(3000.0)


class TestRefusals:
    @pytest.mark.parametrize(
        ("option", "value", "bound"),
        [
            ("min_cellsize_gridpoint", 0, "greater than or equal to 1"),
            ("max_cellsize_gridpoint", 0, "greater than or equal to 1"),
            ("closing_radius", -1, "greater than or equal to 0"),
            ("h_maxima", 0, "greater than 0"),
            ("seed_carry_frames", -1, "greater than or equal to 0"),
            ("seed_carry_min_separation", 0, "greater than or equal to 1"),
        ],
    )
    def test_a_value_outside_an_options_bounds_is_refused(self, path, option, value, bound):
        message = _refusal(path, detection={option: value})

        assert message.splitlines()[0] == f"Invalid configuration (pipeline file {path}):"
        assert (
            f"  • params.detection.{option} — value {value} is out of range — "
            f"Input should be {bound}"
        ) in message.splitlines()

    @pytest.mark.parametrize(
        ("options", "option"),
        [
            ({"method": "watershed"}, "method"),
            (
                {"steiner_conv_strat_params": {"peak_relation": "steep"}},
                "steiner_conv_strat_params.peak_relation",
            ),
            (
                {"steiner_conv_strat_params": {"area_relation": "huge"}},
                "steiner_conv_strat_params.area_relation",
            ),
        ],
    )
    def test_a_value_that_is_not_one_of_an_options_choices_is_refused(self, path, options, option):
        message = _refusal(path, detection=options)

        (line,) = [line for line in message.splitlines() if line.startswith("  • ")]
        assert line.startswith(f"  • params.detection.{option} — Input should be ")

    @pytest.mark.parametrize(
        "option",
        ["threshold", "threshhold", "seed_carry", "seed_carry_max_frames", "closing_kernel"],
    )
    def test_an_option_detection_does_not_have_is_refused(self, path, option):
        message = _refusal(path, detection={option: 1})

        assert f"  • params.detection.{option} — unrecognized option" in message
        assert message.splitlines()[-1] == "The options of detection: adapt config --show detection"

    @pytest.mark.parametrize("method", ["conv_strat_raut", "threshold"])
    def test_a_parameter_a_method_does_not_have_is_refused_whether_or_not_the_run_uses_the_method(
        self, path, method
    ):
        options = {"method": method, "conv_strat_raut_params": {"always_core_thres": 42.0}}

        message = _refusal(path, detection=options)

        assert "params.detection.conv_strat_raut_params.always_core_thres — unrecognized" in message

    def test_the_section_that_held_these_options_is_an_unknown_key(self):
        with pytest.raises(ValueError, match="unknown params key 'segmenter'"):
            _pipeline({"segmenter": {"h_maxima": 2.5}})

    def test_the_unknown_key_message_offers_detection(self):
        with pytest.raises(ValueError) as exc:
            _pipeline({"segmenter": {}})

        modules = str(exc.value).split("the modules ")[1]
        assert "detection" in modules.split(", ")

    def test_options_of_detection_are_refused_when_the_file_does_not_list_it(self):
        with pytest.raises(ValueError, match="params.detection sets the options of detection"):
            _pipeline({"detection": {"h_maxima": 2.5}}, modules=["ingest"])


class TestRunRecord:
    def test_a_run_records_every_option_detection_runs_with(self, path):
        config = run_config(_pipeline({"detection": {"h_maxima": 2.5}}), path)

        recorded = config.module_params["detection"]
        assert recorded == {**DetectionOptions().model_dump(mode="json"), "h_maxima": 2.5}
        assert recorded["threshold_params"] == {"threshold": 35.0}
        assert set(recorded) == set(DetectionOptions.model_fields)

    @pytest.mark.parametrize(
        "given",
        [
            {},
            {
                "method": "steiner_conv_strat",
                "max_cellsize_gridpoint": 400,
                "steiner_conv_strat_params": {"area_relation": "large"},
            },
        ],
    )
    def test_a_run_read_back_from_its_record_configures_detection_the_same(self, path, given):
        config = run_config(_pipeline({"detection": given}), path)

        stored = type(config).model_validate_json(config.model_dump_json())  # runs.config_json

        assert stored.module_params["detection"] == config.module_params["detection"]
        assert resolve_module_configs(stored, ["detection"]) == resolve_module_configs(
            config, ["detection"]
        )

    def test_a_pass_over_a_stored_run_with_other_detection_options_is_refused(self, path):
        recorded = run_config(_pipeline({"detection": {"h_maxima": 2.5}}), path, run_id=_RUN_ID)
        record_run(path.parent, recorded)
        StoreRegistry.get_instance(path.parent).finalize_run(
            _RUN_ID, "completed", scans_processed=0
        )
        later = _pipeline({}, run={"source": "stored_run", "radar": "KBOX", "mode": "historical"})

        with pytest.raises(ConfigError) as exc:
            run_config(later, path, run_id=_RUN_ID)

        assert (
            "  • params.detection.h_maxima — recorded: 2.5; this file: 5.0"
            in str(exc.value).splitlines()
        )
