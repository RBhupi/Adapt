# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""InternalConfig: Authoritative runtime configuration.

This is the ONLY config schema that runtime code sees. It is fully validated,
normalized, and contains NO optional fields that processing code depends on.

All .get() calls, fallback defaults, and validation logic are FORBIDDEN in
runtime code - everything is explicit here.
"""

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from adapt.configuration.schemas.base import AdaptBaseModel
from adapt.configuration.schemas.segmenter_methods import (
    ConvStratRautParams,
    ConvStratYuterParams,
    FeatureDetectionParams,
    SteinerConvStratParams,
    ThresholdParams,
)

# =============================================================================
# Nested Configuration Models (Runtime)
# =============================================================================


class InternalReaderConfig(AdaptBaseModel):
    """Runtime reader configuration."""

    file_format: Literal["nexrad_archive"]
    field_map: dict[str, str]
    fields: list[str]


class InternalDownloaderConfig(AdaptBaseModel):
    """Runtime downloader configuration."""

    mode: Literal["realtime", "historical"]
    radar: str
    output_dir: str  # OBSOLETE — never read (see DownloaderConfig.output_dir)
    latest_files: int
    latest_minutes: int
    poll_interval_sec: int
    max_fetch_retries: int
    start_time: str | None
    end_time: str | None
    min_file_size: int
    max_queue_size: int = 100
    queue_resume_fraction: float = 0.10


class InternalRegridderConfig(AdaptBaseModel):
    """Runtime regridding configuration."""

    grid_shape: tuple[int, int, int]
    grid_limits: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    roi_func: Literal["dist_beam", "dist"]
    min_radius: float
    weighting_function: Literal["cressman", "barnes", "nearest"]


class InternalSegmenterConfig(AdaptBaseModel):
    """Runtime segmentation configuration."""

    method: Literal[
        "conv_strat_raut",
        "conv_strat_yuter",
        "feature_detection",
        "steiner_conv_strat",
        "threshold",
    ]
    min_cellsize_gridpoint: int
    max_cellsize_gridpoint: int | None
    closing_radius: int
    filter_by_size: bool
    h_maxima: float
    seed_carry_frames: int
    seed_carry_min_separation: int
    carried_exempt_size_filter: bool = False
    threshold_params: ThresholdParams
    conv_strat_raut_params: ConvStratRautParams
    conv_strat_yuter_params: ConvStratYuterParams
    feature_detection_params: FeatureDetectionParams
    steiner_conv_strat_params: SteinerConvStratParams

    @model_validator(mode="before")
    @classmethod
    def _validate_only_selected_method_params(cls, data):
        """Only the selected method's params are validated; unused per-method
        blocks are reset to their defaults.

        The generated config carries a block for every method, but a run uses
        exactly one. A stale or renamed field in a method that is not selected
        must never block the pipeline, while a bad param in the selected method
        still fails loudly (its block is validated as-is).
        """
        if not isinstance(data, dict):
            return data
        keep = f"{data.get('method')}_params"
        param_fields = {name for name in cls.model_fields if name.endswith("_params")}
        if keep not in param_fields:
            return data  # unknown/missing method: let normal validation report it
        data = dict(data)
        for field in param_fields - {keep}:
            data[field] = {}  # unused block -> model defaults, ignoring any content
        return data


class InternalCoordNamesConfig(AdaptBaseModel):
    """Runtime coordinate name mappings."""

    time: str
    z: str
    y: str
    x: str


class InternalGlobalConfig(AdaptBaseModel):
    """Runtime global settings."""

    z_level: float
    max_scan_gap_minutes: float = Field(default=10.0, gt=0.0)
    tracking_field: str
    coord_names: InternalCoordNamesConfig


class InternalFlowParamsConfig(AdaptBaseModel):
    """Runtime optical flow parameters."""

    pyr_scale: float
    levels: int
    winsize: int
    iterations: int
    poly_n: int
    poly_sigma: float
    flags: int


class InternalProjectorConfig(AdaptBaseModel):
    """Runtime projection configuration."""

    method: str
    max_projection_steps: int = Field(ge=1, le=10)  # Capped at 10
    nan_fill_value: float
    flow_params: InternalFlowParamsConfig
    min_motion_threshold: float
    max_flow_magnitude: float
    registration_step_minutes: int = Field(ge=1)
    projection_horizon_minutes: int = Field(ge=1)


class InternalAnalyzerConfig(AdaptBaseModel):
    """Runtime analysis configuration."""

    radar_variables: list[str]
    exclude_fields: list[str]
    adjacency_min_touching_boundary_pixels: int = Field(ge=1)


# Tracker v1 keys → the v2 parameter that replaced them. A config written for
# the previous tracker must fail loudly, not run with silently ignored values.
_REMOVED_TRACKER_KEYS = {
    "minimum_candidate_overlap": "max_overlap_mismatch",
    "minimum_projected_overlap": "max_overlap_mismatch",
    "projected_hull_buffer_km": "growth_max_km / growth_size_km",
    "length_scale": "residual_weight",
    "geometry_length_scale_km": "residual_weight",
    "max_speed_multiplier": "max_acceleration_ms2",
    "acceleration_floor_ms": "max_acceleration_ms2",
    "heading_change_penalty_weight": "heading_weight",
}


class InternalTrackerConfig(AdaptBaseModel):
    """Runtime tracking configuration."""

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_keys(cls, data):
        if isinstance(data, dict):
            removed = sorted(k for k in data if k in _REMOVED_TRACKER_KEYS)
            if removed:
                hints = ", ".join(f"{k} → {_REMOVED_TRACKER_KEYS[k]}" for k in removed)
                raise ValueError(
                    f"tracker: {hints} — these keys belong to the previous tracker; "
                    "replace them with the new parameters (see `adapt config --show tracker`)"
                )
        return data

    class InternalCellUidConfig(AdaptBaseModel):
        """Runtime cell UID configuration."""

        width: int = Field(ge=1)
        alphabet: Literal["base36_upper"]  # OBSOLETE — never read (see CellUidConfig.alphabet)

    cell_threshold: float
    polarity: Literal[1, -1]
    growth_max_km: float = Field(ge=0.0)
    growth_size_km: float = Field(gt=0.0)
    max_overlap_mismatch: float = Field(gt=0.0, le=1.0)
    max_link_cost: float = Field(gt=0.0)
    residual_weight: float = Field(ge=0.0)
    heading_weight: float = Field(ge=0.0)
    max_speed_ms: float = Field(gt=0.0)
    max_acceleration_ms2: float = Field(ge=0.0)
    split_overlap_threshold: float = Field(gt=0.0, le=1.0)
    merge_overlap_threshold: float = Field(gt=0.0, le=1.0)
    latent_scans: int = Field(ge=0)
    identity_intensity_weight: float = Field(ge=0.0)
    identity_score_margin: float = Field(ge=0.0)
    core_field_threshold: float
    cell_uid: InternalCellUidConfig


class InternalVisualizationConfig(AdaptBaseModel):
    """Runtime visualization settings."""

    enabled: bool  # OBSOLETE — never read (see VisualizationConfig.enabled)
    dpi: int
    figsize: tuple[float, float]
    output_format: Literal["png", "pdf", "jpeg"]
    use_basemap: bool
    basemap_alpha: float
    seg_linewidth: float
    proj_linewidth: float
    proj_alpha: float
    flow_scale: float
    flow_subsample: int
    min_reflectivity: float
    refl_vmin: float
    refl_vmax: float


class InternalOutputConfig(AdaptBaseModel):
    """Runtime output configuration."""

    compression: Literal["snappy", "gzip", "lz4", "none"]  # OBSOLETE — never read


class InternalLoggingConfig(AdaptBaseModel):
    """Runtime logging + observability configuration.

    ``level`` governs the full file/JSON log; ``console_level`` keeps the console
    quiet independently. The remaining toggles enable/disable the observability
    subsystem and its pillars; the orchestrator translates these into an
    ``ObsSettings`` when it builds the provider.
    """

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    console_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    enabled: bool
    traces: bool
    metrics: bool
    json_logs: bool
    console_logs: bool
    # OBSOLETE — never read: ObsSettings.progress_every has no reader. Remove both.
    progress_every: float = Field(default=30.0, gt=0.0)


class InternalProcessorConfig(AdaptBaseModel):
    """Runtime processor configuration."""

    max_history: int = Field(default=2, ge=2, le=10)  # Frame history for optical flow
    # OBSOLETE — never read: sources filter by downloader.min_file_size.
    min_file_size: int = Field(default=5000, ge=1000)
    # OBSOLETE — never read: the store layout names every database.
    db_filename_pattern: str = Field(default="{radar}_cells_statistics.db")


class PipelineProvenance(AdaptBaseModel):
    """The pipeline file a run was configured from. Recorded only; nothing runs on it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(description="Pipeline name: the file's stem")
    file: str = Field(description="Path of the pipeline file")
    content: dict[str, Any] = Field(
        description="The file's sections as validated: run (launcher settings included), "
        "module lists, consumer sections and params"
    )


# =============================================================================
# Main InternalConfig
# =============================================================================


class InternalConfig(AdaptBaseModel):
    """Authoritative runtime configuration.

    This is the ONLY configuration schema that processing code sees.
    It is fully validated, immutable, and contains explicit values for
    all parameters (no None for fields that runtime depends on).

    Usage
    -----
    Runtime modules receive InternalConfig and access fields directly:

        def __init__(self, config: InternalConfig):
            self.threshold = config.segmenter.threshold  # NOT .get()
            self.z_level = config.global_.z_level

    Rules
    -----
    - NO .get() calls
    - NO fallback defaults
    - NO type checking
    - NO validation

    All of that happens during config resolution, not in runtime code.
    """

    mode: Literal["realtime", "historical"]
    base_dir: str
    source: str = Field(
        default="aws_nexrad",
        description="Name of the registered ingress source plugin that feeds the pipeline",
    )
    source_dir: str | None = Field(
        default=None,
        description="Directory a local source scans for already-present files",
    )
    run_id: str | None = Field(
        default=None,
        description="Unique run identifier, set by the pipeline-file loader",
    )
    # OBSOLETE — never read: the store root (base_dir) lays out every output.
    output_dirs: dict[str, str] | None = None
    reader: InternalReaderConfig
    downloader: InternalDownloaderConfig
    regridder: InternalRegridderConfig
    segmenter: InternalSegmenterConfig
    global_: InternalGlobalConfig = Field(alias="global")
    projector: InternalProjectorConfig
    analyzer: InternalAnalyzerConfig
    tracker: InternalTrackerConfig
    visualization: InternalVisualizationConfig
    output: InternalOutputConfig
    logging: InternalLoggingConfig
    processor: InternalProcessorConfig = Field(default_factory=InternalProcessorConfig)
    module_params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # The live module allowlist: a pipeline file's core + extended modules, in
    # file order (always set explicitly there). None = every registered module.
    modules: list[str] | None = None
    pipeline: PipelineProvenance | None = Field(
        default=None,
        description="The pipeline file this run was configured from; None when the "
        "config was not built from one",
    )

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        use_enum_values=True,
        str_strip_whitespace=True,
        frozen=True,  # Immutable after construction
        populate_by_name=True,  # Allow both 'global' and 'global_'
    )

    def with_module_params(self, params: dict[str, dict[str, Any]]) -> "InternalConfig":
        """This config with ``params`` laid over ``module_params``, module by module.

        A given key wins; the module's other recorded keys are kept.
        """
        merged = {name: dict(options) for name, options in self.module_params.items()}
        for name, options in params.items():
            merged[name] = {**merged.get(name, {}), **options}
        return self.model_copy(update={"module_params": merged})
