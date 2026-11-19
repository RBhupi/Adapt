# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Pin: the values each of the five core modules computes with, every option at its default.

``DEFAULTS`` is every value the module's science code is handed by a run of a
pipeline file that sets no option, under the option's final name, and
``METHOD_PARAMS`` the parameters of each segmentation method. Neither ever
changes.
"""

from typing import Any

import pytest

from adapt.configuration.pipeline_file import PipelineFile
from adapt.configuration.pipeline_run import run_config
from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.contracts import config_key
from adapt.persistence.store import init_store

pytestmark = pytest.mark.unit

DEFAULTS: dict[str, dict[str, Any]] = {
    "ingest": {
        "file_format": "nexrad_archive",
        "field_map": {},
        "fields": (),
        "grid_shape": (41, 301, 301),
        "grid_limits": ((0.0, 20000.0), (-150000.0, 150000.0), (-150000.0, 150000.0)),
        "roi_func": "dist_beam",
        "min_radius": 1750.0,
        "weighting_function": "cressman",
        "z_level": 2000.0,
    },
    "detection": {
        "method": "threshold",
        "method_params": {"threshold": 35.0},
        "closing_radius": 2,
        "filter_by_size": True,
        "min_cellsize_gridpoint": 5,
        "max_cellsize_gridpoint": None,
        "h_maxima": 5.0,
        "seed_carry_frames": 1,
        "seed_carry_min_separation": 5,
        "carried_exempt_size_filter": False,
        "reflectivity_var": "reflectivity",
        "z_level": 2000.0,
    },
    "projection": {
        "method": "adapt_default",
        "nan_fill_value": 0.0,
        "max_time_interval_minutes": 10.0,
        "max_projection_steps": 3,
        "flow_params.pyr_scale": 0.5,
        "flow_params.levels": 3,
        "flow_params.winsize": 10,
        "flow_params.iterations": 3,
        "flow_params.poly_n": 7,
        "flow_params.poly_sigma": 1.5,
        "flow_params.flags": 0,
        "min_motion_threshold": 1.0,
        "max_flow_magnitude": 20.0,
        "registration_step_minutes": 1,
        "projection_horizon_minutes": 15,
        "reflectivity_var": "reflectivity",
    },
    "analysis": {
        "radar_variables": (
            "reflectivity",
            "velocity",
            "differential_phase",
            "differential_reflectivity",
            "spectrum_width",
            "cross_correlation_ratio",
        ),
        "exclude_fields": (
            "ROI",
            "labels",
            "cell_labels",
            "cell_projections",
            "clutter_filter_power_removed",
        ),
        "adjacency_min_touching_boundary_pixels": 1,
        "reflectivity_var": "reflectivity",
        "z_level": 2000.0,
    },
    "tracking": {
        "uid_width": 10,
        "field_var": "reflectivity",
        "max_tracking_gap_minutes": 10.0,
        "cell_threshold": 30.0,
        "polarity": 1,
        "growth_max_km": 2.0,
        "growth_size_km": 12.0,
        "max_overlap_mismatch": 0.6,
        "max_link_cost": 1.8,
        "residual_weight": 0.25,
        "heading_weight": 1.0,
        "max_speed_ms": 40.0,
        "max_acceleration_ms2": 0.04,
        "split_overlap_threshold": 0.65,
        "merge_overlap_threshold": 0.7,
        "latent_scans": 2,
        "identity_intensity_weight": 1.0,
        "identity_score_margin": 0.33,
        "core_field_threshold": 40.0,
    },
}

# The parameters handed to each segmentation method, in the order they are
# written into the labels' NetCDF attributes.
METHOD_PARAMS: dict[str, dict[str, Any]] = {
    "threshold": {"threshold": 35.0},
    "conv_strat_raut": {
        "zr_a": 200.0,
        "zr_b": 1.6,
        "core_wt_threshold": 5.0,
        "conv_wt_threshold": 1.5,
        "conv_scale_km": 25.0,
        "min_reflectivity": 5.0,
        "conv_min_refl": 25.0,
        "conv_core_threshold": 42.0,
    },
    "conv_strat_yuter": {
        "always_core_thres": 42.0,
        "bkg_rad_km": 11.0,
        "use_cosine": True,
        "max_diff": 5.0,
        "zero_diff_cos_val": 55.0,
        "scalar_diff": 1.5,
        "use_addition": True,
        "calc_thres": 0.75,
        "weak_echo_thres": 5.0,
        "min_dBZ_used": 5.0,
        "dB_averaging": True,
        "remove_small_objects": True,
        "min_km2_size": 10.0,
        "val_for_max_conv_rad": 30.0,
        "max_conv_rad_km": 5.0,
    },
    "feature_detection": {
        "always_core_thres": 42.0,
        "bkg_rad_km": 11.0,
        "use_cosine": True,
        "max_diff": 5.0,
        "zero_diff_cos_val": 55.0,
        "scalar_diff": 1.5,
        "use_addition": True,
        "calc_thres": 0.75,
        "weak_echo_thres": 5.0,
        "min_val_used": 5.0,
        "dB_averaging": True,
        "remove_small_objects": True,
        "min_km2_size": 10.0,
        "binary_close": False,
        "val_for_max_rad": 30.0,
        "max_rad_km": 5.0,
    },
    "steiner_conv_strat": {
        "intense": 42.0,
        "peak_relation": "default",
        "area_relation": "medium",
        "bkg_rad": 11000.0,
        "use_intense": True,
    },
}


def _module_config(tmp_path, name: str, params: dict[str, dict[str, Any]]) -> Any:
    """The config module ``name`` runs with in a new run of a pipeline file with ``params``."""
    pipeline = PipelineFile.model_validate(
        {
            "run": {"source": "aws_nexrad", "radar": "KTST", "mode": "realtime"},
            "modules": list(DEFAULTS),
            "params": params,
        }
    )
    config = run_config(pipeline, init_store(tmp_path / "store") / "case.yaml")
    return resolve_module_configs(config, [name])[config_key(name)]


def _held(config: Any, option: str) -> Any:
    """The value ``config`` holds for ``option`` (a dotted name reads a nested part)."""
    value = config
    for part in option.split("."):
        value = getattr(value, part)
    return value


def _exact(value: Any) -> Any:
    """``value`` in a form whose equality also tells number types apart (2000 is
    not 2000.0, 1 is not True) and compares a mapping's key order. Whether a
    sequence is a tuple or a list is not compared."""
    if isinstance(value, dict):
        return ("mapping", [(key, _exact(item)) for key, item in value.items()])
    if isinstance(value, list | tuple):
        return [_exact(item) for item in value]
    return (type(value).__name__, value)


@pytest.mark.parametrize("name", list(DEFAULTS))
def test_default_values_reach_the_module_unchanged(tmp_path, name):
    config = _module_config(tmp_path, name, params={})

    held = {option: _exact(_held(config, option)) for option in DEFAULTS[name]}

    assert held == {option: _exact(value) for option, value in DEFAULTS[name].items()}


@pytest.mark.parametrize("method", list(METHOD_PARAMS))
def test_each_segmentation_method_gets_its_default_parameters_in_order(tmp_path, method):
    config = _module_config(tmp_path, "detection", params={"detection": {"method": method}})

    assert _exact(config.method_params) == _exact(METHOD_PARAMS[method])
