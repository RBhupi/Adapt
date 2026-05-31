# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Per-module config materialization.

Slices the frozen InternalConfig into one lightweight frozen dataclass per
pipeline module. Called once at processor startup; the resulting objects are
injected into executor contexts under module-specific keys.

Shared fields (global_, cross-module references) are copied by value so each
module config is self-contained and independent of all others.
"""

from __future__ import annotations

from dataclasses import dataclass

from adapt.configuration.schemas.internal import InternalConfig


@dataclass(frozen=True)
class IngestModuleConfig:
    file_format: str
    grid_shape: tuple
    grid_limits: tuple
    roi_func: str
    min_radius: float
    weighting_function: str
    save_netcdf: bool
    radar: str
    z_level: float
    z_coord: str
    time_coord: str


@dataclass(frozen=True)
class DetectionModuleConfig:
    method: str
    threshold: float
    closing_kernel: tuple
    filter_by_size: bool
    min_cellsize_gridpoint: int
    max_cellsize_gridpoint: int | None
    h_maxima: float
    reflectivity_var: str
    labels_var: str
    z_level: float


@dataclass(frozen=True)
class ProjectionModuleConfig:
    method: str
    nan_fill_value: float
    max_time_interval_minutes: int
    max_projection_steps: int
    pyr_scale: float
    levels: int
    winsize: int
    iterations: int
    poly_n: int
    poly_sigma: float
    flags: int
    min_motion_threshold: float
    max_flow_magnitude: float
    reflectivity_var: str


@dataclass(frozen=True)
class AnalysisModuleConfig:
    radar_variables: tuple
    exclude_fields: tuple
    adjacency_min_touching: int
    max_projection_steps: int
    reflectivity_var: str
    labels_var: str
    z_level: float


@dataclass(frozen=True)
class TrackingModuleConfig:
    match_cost: float
    keep_cost: float
    unmatch_cost: float
    split_overlap: float
    core_reflectivity_threshold: float
    uid_time_step_s: int
    uid_latlon_step_deg: float
    uid_area_step_km2: float
    uid_width: int
    reflectivity_var: str
    labels_var: str
    max_gap_minutes: float
    expected_speed_ms: float


def materialize_module_configs(cfg: InternalConfig) -> dict:
    """Slice InternalConfig into one frozen config per module.

    Returns a dict keyed by the context key each module declares in
    its ``inputs`` list. Shared values (global_, cross-module) are copied
    by value — no module config holds a reference to another.
    """
    return {
        "ingest_config": IngestModuleConfig(
            file_format=cfg.reader.file_format,
            grid_shape=cfg.regridder.grid_shape,
            grid_limits=cfg.regridder.grid_limits,
            roi_func=cfg.regridder.roi_func,
            min_radius=cfg.regridder.min_radius,
            weighting_function=cfg.regridder.weighting_function,
            save_netcdf=cfg.regridder.save_netcdf,
            radar=cfg.downloader.radar,
            z_level=cfg.global_.z_level,
            z_coord=cfg.global_.coord_names.z,
            time_coord=cfg.global_.coord_names.time,
        ),
        "detection_config": DetectionModuleConfig(
            method=cfg.segmenter.method,
            threshold=cfg.segmenter.threshold,
            closing_kernel=cfg.segmenter.closing_kernel,
            filter_by_size=cfg.segmenter.filter_by_size,
            min_cellsize_gridpoint=cfg.segmenter.min_cellsize_gridpoint,
            max_cellsize_gridpoint=cfg.segmenter.max_cellsize_gridpoint,
            h_maxima=cfg.segmenter.h_maxima,
            reflectivity_var=cfg.global_.var_names.reflectivity,
            labels_var=cfg.global_.var_names.cell_labels,
            z_level=cfg.global_.z_level,
        ),
        "projection_config": ProjectionModuleConfig(
            method=cfg.projector.method,
            nan_fill_value=cfg.projector.nan_fill_value,
            max_time_interval_minutes=cfg.projector.max_time_interval_minutes,
            max_projection_steps=cfg.projector.max_projection_steps,
            pyr_scale=cfg.projector.flow_params.pyr_scale,
            levels=cfg.projector.flow_params.levels,
            winsize=cfg.projector.flow_params.winsize,
            iterations=cfg.projector.flow_params.iterations,
            poly_n=cfg.projector.flow_params.poly_n,
            poly_sigma=cfg.projector.flow_params.poly_sigma,
            flags=cfg.projector.flow_params.flags,
            min_motion_threshold=cfg.projector.min_motion_threshold,
            max_flow_magnitude=cfg.projector.max_flow_magnitude,
            reflectivity_var=cfg.global_.var_names.reflectivity,
        ),
        "analysis_config": AnalysisModuleConfig(
            radar_variables=tuple(cfg.analyzer.radar_variables),
            exclude_fields=tuple(cfg.analyzer.exclude_fields),
            adjacency_min_touching=cfg.analyzer.adjacency_min_touching_boundary_pixels,
            max_projection_steps=cfg.projector.max_projection_steps,
            reflectivity_var=cfg.global_.var_names.reflectivity,
            labels_var=cfg.global_.var_names.cell_labels,
            z_level=cfg.global_.z_level,
        ),
        "tracking_config": TrackingModuleConfig(
            match_cost=cfg.tracker.match_cost_threshold,
            keep_cost=cfg.tracker.keep_cost_threshold,
            unmatch_cost=cfg.tracker.unmatch_cost_threshold,
            split_overlap=cfg.tracker.split_overlap_threshold,
            core_reflectivity_threshold=cfg.tracker.core_reflectivity_threshold,
            uid_time_step_s=cfg.tracker.cell_uid.time_step_s,
            uid_latlon_step_deg=cfg.tracker.cell_uid.latlon_step_deg,
            uid_area_step_km2=cfg.tracker.cell_uid.area_step_km2,
            uid_width=cfg.tracker.cell_uid.width,
            reflectivity_var=cfg.global_.var_names.reflectivity,
            labels_var=cfg.global_.var_names.cell_labels,
            max_gap_minutes=cfg.tracker.max_gap_minutes,
            expected_speed_ms=cfg.tracker.expected_speed_ms,
        ),
    }
