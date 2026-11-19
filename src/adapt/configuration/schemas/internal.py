# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""InternalConfig: the frozen run config.

This is the ONLY config schema that runtime code sees. It is fully validated
and holds an explicit value for everything a run depends on, so runtime code
never calls ``.get()``, applies a fallback or validates again. It is built from
a pipeline file by ``adapt.configuration.pipeline_run`` and recorded with the
run.

Its ``downloader``, ``global`` and ``logging`` sections are also the options a
pipeline file sets under ``params:`` with those names: each option's default,
bounds and description are defined here, once. A module's options are the
module's own; the run config records them under ``module_params``.
"""

from typing import Any, Literal

from pydantic import ConfigDict, Field

from adapt.configuration.schemas.base import AdaptBaseModel

_LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# =============================================================================
# Sections
# =============================================================================


class InternalDownloaderConfig(AdaptBaseModel):
    """The scan source's settings (``params.downloader``).

    ``mode``, ``radar``, ``start_time`` and ``end_time`` are the pipeline file's
    ``run:`` section's; ``params.downloader`` cannot set them.
    """

    model_config = ConfigDict(frozen=True)

    mode: Literal["realtime", "historical"] = Field(
        description="realtime follows new scans; historical processes "
        "start_time..end_time. A pipeline file sets it as run.mode"
    )
    radar: str = Field(
        description="Radar / collection id to fetch scans for; a pipeline file sets it as run.radar"
    )
    latest_files: int = Field(
        default=5,
        ge=1,
        description="Realtime: newest scans taken from each poll's latest_minutes window",
    )
    latest_minutes: int = Field(
        default=60, ge=1, description="Realtime: how far back (minutes) each poll looks for scans"
    )
    poll_interval_sec: int = Field(default=300, ge=1, description="Polling interval in seconds")
    max_fetch_retries: int = Field(
        default=3, ge=1, description="AWS scan-fetch attempts before giving up for this poll"
    )
    start_time: str | None = Field(
        description="Historical: first scan time (ISO 8601 UTC); a pipeline file sets it as "
        "run.start_time"
    )
    end_time: str | None = Field(
        description="Historical: last scan time (ISO 8601 UTC); a pipeline file sets it as "
        "run.end_time"
    )
    min_file_size: int = Field(
        default=1024, ge=1, description="Minimum file size in bytes to consider valid"
    )
    max_queue_size: int = Field(
        default=100,
        ge=1,
        description=(
            "Scans the downloader may run ahead of the processor; at this many queued "
            "scans the downloader pauses, while processing never pauses. Each queued "
            "scan costs only the disk of its downloaded volume, about 10 MB"
        ),
    )
    # The gap between the pause and resume levels stops the downloader flapping on
    # every consumed scan.
    queue_resume_fraction: float = Field(
        default=0.10,
        gt=0.0,
        lt=1.0,
        description=(
            "The downloader resumes once the queue has drained to this fraction of "
            "max_queue_size (at least 1 scan)"
        ),
    )


class InternalGlobalConfig(AdaptBaseModel):
    """The settings of the whole run (``params.global``), shared by its modules."""

    model_config = ConfigDict(frozen=True)

    # The run is recorded as JSON, which holds no infinity or NaN: a run with
    # one could not be read back to resume it.
    z_level: float = Field(
        default=2000.0, allow_inf_nan=False, description="Analysis altitude in meters"
    )
    max_scan_gap_minutes: float = Field(
        default=10.0,
        gt=0.0,
        allow_inf_nan=False,
        description=(
            "Largest gap (minutes) between two scans that any stage pairs across; beyond "
            "it no seed is carried, no projection is computed and tracking resets. Set it "
            "to roughly twice the nominal scan cadence; one shared value keeps the stages "
            "from disagreeing about whether a gap is crossable"
        ),
    )
    tracking_field: str = Field(
        default="reflectivity",
        description=(
            "Canonical field that drives detection, projection, and "
            "tracking. Any canonical variable present after ingest "
            "(see ingest.field_map / fields) is valid."
        ),
    )


class InternalLoggingConfig(AdaptBaseModel):
    """Logging and observability settings (``params.logging``).

    ``level`` governs the run's log file; ``console_level`` keeps the console
    quiet independently. The remaining toggles enable/disable the observability
    subsystem and its pillars; the orchestrator translates these into an
    ``ObsSettings`` when it builds the provider.
    """

    model_config = ConfigDict(frozen=True)

    level: _LogLevel = Field(
        default="INFO",
        description="Level of the run's log file (logs/pipeline_<radar>.log); the "
        "console has its own console_level",
    )
    console_level: _LogLevel = Field(
        default="WARNING", description="Level of the log messages printed on the console"
    )
    console_logs: bool = Field(
        default=True,
        description="Print log messages on the console, with a live status line when "
        "it is a terminal",
    )
    json_logs: bool = Field(
        default=False, description="Write the run's log file as JSON lines, not text"
    )
    enabled: bool = Field(
        default=True,
        description="Collect the run's telemetry; off, traces and metrics are off too",
    )
    traces: bool = Field(
        default=True,
        description="Record each module's timing span, kept as the run's module history",
    )
    metrics: bool = Field(
        default=True,
        description="Count files, scans, cells and timings for the end-of-run summary",
    )


class PipelineProvenance(AdaptBaseModel):
    """The pipeline file a run was configured from. Recorded only; nothing runs on it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(description="Pipeline name: the file's stem")
    file: str = Field(description="Path of the pipeline file")
    content: dict[str, Any] = Field(
        description="The file's sections as validated: run (launcher settings included), "
        "modules and params"
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
    Runtime code receives InternalConfig and accesses fields directly:

        def __init__(self, config: InternalConfig):
            self.radar = config.downloader.radar  # NOT .get()
            self.z_level = config.global_.z_level

    Rules
    -----
    - NO .get() calls
    - NO fallback defaults
    - NO type checking
    - NO validation

    All of that happens when the run is configured, not in runtime code.
    """

    mode: Literal["realtime", "historical"]
    base_dir: str
    source: str = Field(
        description="Name of the registered ingress source plugin that feeds the pipeline"
    )
    source_dir: str | None = Field(
        default=None,
        description="Directory a local source scans for already-present files",
    )
    run_id: str = Field(
        description="The run's id (YYYYMONDD-HHMM-RADAR). Empty in a config built only to "
        "wire a file's modules (adapt graph), which starts no run"
    )
    downloader: InternalDownloaderConfig
    global_: InternalGlobalConfig = Field(alias="global")
    logging: InternalLoggingConfig
    module_params: dict[str, dict[str, Any]] = Field(
        description="The validated options of every listed module that has options, "
        "complete, by module name"
    )
    modules: list[str] = Field(
        description="The run's modules: a pipeline file's 'modules:' list, in file order"
    )
    pipeline: PipelineProvenance = Field(
        description="The pipeline file this run was configured from"
    )

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        use_enum_values=True,
        str_strip_whitespace=True,
        frozen=True,  # Immutable after construction
        populate_by_name=True,  # Allow both 'global' and 'global_'
    )
