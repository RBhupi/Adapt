# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The pipeline file: one YAML file per pipeline, ``<store root>/<name>.yaml``.

Sections: ``run`` (where scans come from), ``modules`` (the per-scan modules to
run, by name, checked against the module catalog; Adapt orders them by their
inputs and outputs) and ``params`` (a section of the run config — ``global``,
``downloader``, ``logging`` — or the name of a listed module -> options). The
options themselves are checked when a run of the file is configured, see
``pipeline_run``: a section's by the run config's model of it, a module's by
the module's own options model.

``load_pipeline_file`` reads and validates a file; ``pipeline_run`` turns it
into a run's config. The folder that holds the file is the store root.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    PositiveInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

from adapt.configuration.schemas.base import AdaptBaseModel
from adapt.configuration.schemas.errors import ConfigError, validated
from adapt.configuration.schemas.internal import (
    InternalDownloaderConfig,
    InternalGlobalConfig,
    InternalLoggingConfig,
)
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import catalog_module_names

__all__ = [
    "RUN_OWNED_DOWNLOADER_KEYS",
    "PipelineFile",
    "RunSection",
    "load_pipeline_file",
    "param_section_models",
]

# Downloader settings the run section owns; ``params.downloader`` must not set them.
RUN_OWNED_DOWNLOADER_KEYS = ("radar", "mode", "start_time", "end_time")

# The source that reads a folder (``run.source_dir``) instead of fetching scans.
_LOCAL_SOURCE = "local_directory"
# The source that walks the scans of a run the store already has (``--run-id``).
_STORED_SOURCE = "stored_run"

_FROZEN = AdaptBaseModel.model_config.copy()
_FROZEN.update({"frozen": True})


def param_section_models() -> dict[str, type[BaseModel]]:
    """The ``params:`` keys that are sections of the run config, each with the
    model of its options: the three settings ``InternalConfig`` holds under
    these names."""
    return {
        "downloader": InternalDownloaderConfig,
        "global": InternalGlobalConfig,
        "logging": InternalLoggingConfig,
    }


class RunSection(AdaptBaseModel):
    """The ``run:`` section: where scans come from, and launcher settings."""

    model_config = _FROZEN

    source: str = Field(
        description="where scans come from: aws_nexrad | local_directory | stored_run"
    )
    radar: str = Field(description="radar / collection id")
    mode: Literal["realtime", "historical"] = Field(description="realtime | historical")
    start_time: AwareDatetime | None = Field(
        default=None, description="historical: ISO 8601 UTC, e.g. 2025-06-17T18:00:00Z"
    )
    end_time: AwareDatetime | None = None
    source_dir: str | None = Field(
        default=None, description="local_directory: folder of Level-II files"
    )
    max_runtime_minutes: PositiveInt | None = Field(
        default=None, description="realtime: stop after this many minutes; null = until Ctrl-C"
    )

    @field_validator("start_time", "end_time")
    @classmethod
    def _in_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def _settings_fit_the_mode_and_source(self) -> "RunSection":
        # A setting the run would ignore is refused, never dropped silently.
        times = [f"run.{k}" for k in ("start_time", "end_time") if getattr(self, k) is not None]
        if times and self.mode != "historical":
            raise ValueError(
                f"{' and '.join(times)} apply to mode: historical only; this run is "
                f"mode: {self.mode} — set mode: historical or remove them"
            )
        if self.max_runtime_minutes is not None and self.mode != "realtime":
            raise ValueError(
                "run.max_runtime_minutes applies to mode: realtime only (a historical "
                "run ends after end_time) — remove it"
            )
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValueError("run.end_time must be after run.start_time")
        if self.source == _LOCAL_SOURCE and self.source_dir is None:
            raise ValueError(
                f"run.source_dir is required for source: {_LOCAL_SOURCE} — the folder "
                "of Level-II files to process"
            )
        if self.source == _LOCAL_SOURCE and self.mode != "historical":
            raise ValueError(
                f"source: {_LOCAL_SOURCE} processes the files already in run.source_dir, "
                "so it needs mode: historical (with run.start_time and run.end_time)"
            )
        if self.source == _STORED_SOURCE and (self.mode != "historical" or times):
            raise ValueError(
                f"source: {_STORED_SOURCE} walks the scans of the run named by --run-id, "
                "all of them: it needs mode: historical and takes no run.start_time or "
                "run.end_time"
            )
        if self.source != _LOCAL_SOURCE and self.source_dir is not None:
            raise ValueError(
                f"run.source_dir applies to source: {_LOCAL_SOURCE} only (this file has "
                f"source: {self.source}) — set source: {_LOCAL_SOURCE}, or remove source_dir"
            )
        return self


class PipelineFile(AdaptBaseModel):
    """A validated pipeline file. Every section is required; ``null`` means empty."""

    model_config = _FROZEN

    run: RunSection
    modules: tuple[str, ...]
    params: dict[str, dict[str, Any]]

    @field_validator("modules", mode="before")
    @classmethod
    def _null_is_an_empty_list(cls, value: Any) -> Any:
        # A list whose items are all commented out parses as YAML null.
        return [] if value is None else value

    @field_validator("params", mode="before")
    @classmethod
    def _null_is_an_empty_mapping(cls, value: Any) -> Any:
        # Likewise a mapping, or one entry's options, all commented out.
        if value is None:
            return {}
        if isinstance(value, dict):
            return {key: {} if options is None else options for key, options in value.items()}
        return value

    @field_validator("modules")
    @classmethod
    def _modules_are_registered(cls, names: tuple[str, ...]) -> tuple[str, ...]:
        catalog_module_names()  # registers every catalog module
        known = registry.list_modules()
        if not names:
            raise ValueError(f"'modules:' lists no module. Modules: {', '.join(known)}")
        for name in names:
            if name not in known:
                raise ValueError(
                    f"unknown module '{name}' under 'modules:'. Modules: {', '.join(known)}"
                )
        repeated = sorted({n for n in names if names.count(n) > 1})
        if repeated:
            raise ValueError(f"module(s) {', '.join(repeated)} listed more than once")
        return names

    @field_validator("params")
    @classmethod
    def _params_are_sections_or_listed_modules(
        cls, params: dict[str, dict[str, Any]], info: ValidationInfo
    ) -> dict[str, dict[str, Any]]:
        sections = tuple(param_section_models())
        catalog_module_names()  # registers every catalog module
        modules = sorted(registry.list_modules())  # each sets its options under its name
        for key in params:
            if key in modules:
                # "modules" is absent from info.data when it failed its own validation.
                if "modules" in info.data and key not in info.data["modules"]:
                    raise ValueError(
                        f"params.{key} sets the options of {key}, which this file does not "
                        f"run — list it under 'modules:', or remove params.{key}"
                    )
            elif key not in sections:
                raise ValueError(
                    f"unknown params key '{key}'. Valid keys: the sections "
                    f"{', '.join(sections)}; the modules {', '.join(modules)}"
                )
        owned = [k for k in RUN_OWNED_DOWNLOADER_KEYS if k in params.get("downloader", {})]
        if owned:
            raise ValueError(
                ", ".join(f"params.downloader.{k}" for k in owned)
                + " belong to the run section — set "
                + ", ".join(f"run.{k}" for k in owned)
                + " instead"
            )
        return params


class _RepeatedKey(ValueError):
    """A mapping of the file gives the same key twice."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """A safe loader that refuses a repeated key; plain PyYAML keeps the last one silently."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        # The keys as written: constructing folds any '<<' merge into node.value.
        written = [key for key, _ in node.value if key.tag != "tag:yaml.org,2002:merge"]
        mapping = super().construct_mapping(node, deep=deep)
        lines: dict[Any, int] = {}
        for key_node in written:
            key = self.construct_object(key_node, deep=deep)
            line = key_node.start_mark.line + 1
            if key in lines:
                raise _RepeatedKey(
                    f"key {key!r} is given twice (lines {lines[key]} and {line}); "
                    "merge the two into one"
                )
            lines[key] = line
        return mapping


def load_pipeline_file(path: str | Path) -> PipelineFile:
    """Read and validate the pipeline file at ``path``.

    Raises
    ------
    FileNotFoundError
        ``path`` does not exist.
    ConfigError
        The file is not YAML, gives a key twice, is not a mapping of sections, or
        fails validation — the message names the file and each offending key.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)  # a SafeLoader: no object tags
    except _RepeatedKey as exc:
        raise ConfigError(f"Pipeline file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Pipeline file {path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(
            f"Pipeline file {path} must be a mapping of sections (run, modules, params); "
            f"got {type(data).__name__}"
        )
    return validated(PipelineFile, data, source=f"pipeline file {path}")
