# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""A run's config from a pipeline file: ``run_config``.

``run_config`` turns a loaded file into the run's ``InternalConfig`` through the
internal merge layer (``resolve_config(ParamConfig(), UserConfig)``), including
run-id generation, the reload of a stored run's config for ``--run-id``, and the
provenance record of the file. It configures every module the run will run and
builds their graph, so a module that refuses its options or lacks an input
fails before anything starts.
"""

import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from adapt.configuration.pipeline_file import PipelineFile, param_section_names
from adapt.configuration.schemas.errors import ConfigError, validated
from adapt.configuration.schemas.internal import InternalConfig, PipelineProvenance
from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.configuration.schemas.param import ParamConfig
from adapt.configuration.schemas.resolve import resolve_config
from adapt.configuration.schemas.user import UserConfig
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import resolve_enabled_modules, scan_graph
from adapt.persistence.errors import StoreError
from adapt.persistence.store_registry import StoreRegistry
from adapt.utils.time import to_scan_iso

__all__ = ["configured_modules", "run_config"]

_STORED_SOURCE = "stored_run"

_RUN_ID_PATTERN = re.compile(
    r"^\d{4}(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2}-\d{4}-[A-Z0-9]{4}$"
)


def run_config(pipeline: PipelineFile, path: Path, run_id: str | None = None) -> InternalConfig:
    """The ``InternalConfig`` of one run of ``pipeline``, read from the file at ``path``.

    The store root is the file's folder. Without ``run_id`` the run gets a new id
    (``YYYYMONDD-HHMM-RADAR``, local wall clock), refused when the store already
    has a run of that id. A ``run_id`` of a run already in the store returns that
    run's recorded config unchanged: the run is resumed with its recorded modules
    and params, and of the file only ``run.max_runtime_minutes`` applies. An
    unknown ``run_id`` configures a new run with that id, which must end with
    ``-<run.radar>``. A new run records the file (``InternalConfig.pipeline``)
    and, in ``module_params``, the params of every listed module that owns its
    options, with their defaults.

    ``run.source: stored_run`` runs the file's modules over the stored run
    ``run_id``: that run's recorded config, with the file's module list and
    those modules' own options.

    Every module the run will run is configured here and wired into its graph,
    so a module that refuses its options or misses an input fails now, not
    mid-run.

    Raises
    ------
    StoreError
        The file's folder is not an initialized store.
    ConfigError
        The new run's id is taken; ``run_id`` does not match
        ``YYYYMONDD-HHMM-RADAR``, names a run of another radar, or names a new
        run without the file's radar; a stored run is not named, unknown or
        still running; or the file's settings do not configure every module.
    """
    if pipeline.run.source == _STORED_SOURCE:
        config = _stored_run_config(pipeline, path, run_id)
    elif run_id is None:
        config = _internal_config(pipeline, path, _new_run_id(pipeline, path))
    else:
        config = _named_run_config(pipeline, path, run_id)
    _configure(path, config)
    return config


def configured_modules(pipeline: PipelineFile, path: Path) -> list:
    """The file's modules as a new run of it would wire them, in catalog order.

    Each reads what the file's options make it read. No store is read: a file
    over a stored run is wired with its own params, not that run's recorded ones.

    Raises
    ------
    ConfigError
        The file's settings do not configure every module, or an input has no source.
    """
    return _configure(path, _internal_config(pipeline, path, run_id=""))


def _configure(path: Path, config: InternalConfig) -> list:
    """Build every listed module's config from ``config`` and wire their graph."""
    try:
        enabled = resolve_enabled_modules(registry.create_modules(), config.modules)
        configs = resolve_module_configs(config, [m.name for m in enabled])
        scan_graph(enabled, configs, stored_run=config.source == _STORED_SOURCE)
    except ValueError as exc:  # a module refused (pydantic's ValidationError is one)
        raise ConfigError(f"Pipeline file {path}: {exc}") from exc
    return enabled


def _stored_run_config(pipeline: PipelineFile, path: Path, run_id: str | None) -> InternalConfig:
    """Stored run ``run_id``'s recorded config, running the file's modules with their options."""
    if run_id is None:
        raise ConfigError(
            f"Pipeline file {path} has run.source: {_STORED_SOURCE}, so it runs over a run "
            "the store already has: name that run with --run-id"
        )
    radar = pipeline.run.radar
    run_registry = StoreRegistry.get_instance(path.parent)
    try:
        run = run_registry.get_run(run_id)
    except StoreError as exc:
        runs = ", ".join(r["run_id"] for r in run_registry.list_runs(radar)) or "(none)"
        raise ConfigError(
            f"{str(exc).rstrip('.')}. Runs of radar {radar} in this store: {runs}"
        ) from exc
    recorded = _recorded_config(run, path)
    if recorded.downloader.radar != radar:
        raise ConfigError(
            f"Run {run_id} is of radar '{recorded.downloader.radar}', but {path} has "
            f"run.radar '{radar}'"
        )
    if run["status"] == "running":
        raise ConfigError(
            f"Run {run_id} is still running; run over it once it has ended (a run "
            f"left running by a killed pipeline ends when its own file resumes it with "
            f"--run-id {run_id})"
        )
    sections = [key for key in pipeline.params if key in param_section_names()]
    if sections:
        raise ConfigError(
            f"Pipeline file {path}: {', '.join(f'params.{s}' for s in sections)} cannot "
            f"change over a stored run, which keeps the settings it was made with — "
            "remove them (the options of the listed modules that own theirs still apply)"
        )
    print(f"Running {', '.join(pipeline.modules)} over stored run {run_id}")
    return recorded.with_module_params(_owned_params(pipeline)).model_copy(
        update={
            "source": _STORED_SOURCE,
            "mode": "historical",
            "modules": list(pipeline.modules),
            "pipeline": _provenance(pipeline, path),
        }
    )


def _owned_params(pipeline: PipelineFile) -> dict[str, dict[str, Any]]:
    """The effective params of every listed module that owns its options (the file
    gives params to listed modules only), defaults included, so the run record
    states them; as JSON values (tuples as lists)."""
    as_json = TypeAdapter(dict[str, Any])
    return {
        name: as_json.dump_python(
            {**registry.get(name).default_params(), **pipeline.params.get(name, {})}, mode="json"
        )
        for name in pipeline.modules
        if not registry.get(name).param_sections
    }


def _named_run_config(pipeline: PipelineFile, path: Path, run_id: str) -> InternalConfig:
    """The config of run ``run_id``: the recorded one if the store has it, else a new run's."""
    if _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ConfigError(
            f"--run-id must match YYYYMONDD-HHMM-RADAR (e.g. 2026MAR23-0206-KBOX); got '{run_id}'"
        )
    run_registry = StoreRegistry.get_instance(path.parent)
    try:
        run = run_registry.get_run(run_id)
    except StoreError:
        radar = pipeline.run.radar.upper()
        if not run_id.endswith(f"-{radar}"):
            raise ConfigError(
                f"--run-id {run_id} names a new run, so it must end with -{radar}, the "
                f"run.radar of {path} (format YYYYMONDD-HHMM-RADAR)"
            ) from None
        print(f"Using user-provided run ID (new run): {run_id}")
        return _internal_config(pipeline, path, run_id)
    recorded = _recorded_config(run, path)
    if recorded.downloader.radar != pipeline.run.radar:
        raise ConfigError(
            f"Run {run_id} is of radar '{recorded.downloader.radar}', but {path} has "
            f"run.radar '{pipeline.run.radar}'"
        )
    print(f"Continuing existing run ID: {run_id}")
    print(
        "It keeps its recorded modules and params; of the pipeline file only "
        "run.max_runtime_minutes applies."
    )
    return recorded


def _recorded_config(run: Mapping[str, Any], path: Path) -> InternalConfig:
    """The config a stored run was made with, in the store as it is now: the
    file's folder, wherever the store was when the run was recorded."""
    recorded = InternalConfig.model_validate_json(run["config_json"])
    return recorded.model_copy(update={"base_dir": str(path.parent)})


def _new_run_id(pipeline: PipelineFile, path: Path) -> str:
    """A new run's ID, YYYYMONDD-HHMM-RADAR (e.g. 2026MAR23-0206-KBOX), from the clock.

    It never names a run of the store: only ``--run-id`` continues one.
    """
    now_local = datetime.now()
    month = now_local.strftime("%b").upper()
    radar = pipeline.run.radar.upper()
    run_id = f"{now_local:%Y}{month}{now_local:%d}-{now_local:%H%M}-{radar}"
    run_registry = StoreRegistry.get_instance(path.parent)
    try:
        run_registry.get_run(run_id)
    except StoreError:
        return run_id
    raise ConfigError(
        f"A run with id {run_id} was started this minute (a new run's id is the minute it "
        f"starts): wait a minute, or name a new run with --run-id YYYYMONDD-HHMM-{radar}"
    )


def _internal_config(pipeline: PipelineFile, path: Path, run_id: str) -> InternalConfig:
    """Translate the file into the internal merge layer's input and resolve it."""
    run = pipeline.run
    sections = {k: v for k, v in pipeline.params.items() if k in param_section_names()}
    downloader: dict[str, Any] = {**sections.pop("downloader", {}), "radar": run.radar}
    if run.start_time is not None:
        downloader["start_time"] = to_scan_iso(run.start_time)
    if run.end_time is not None:
        downloader["end_time"] = to_scan_iso(run.end_time)
    user = {
        **sections,
        "base_dir": str(path.parent),
        "mode": run.mode,
        "source": run.source,
        "source_dir": run.source_dir,
        "downloader": downloader,
        "modules": list(pipeline.modules),
        "module_params": _owned_params(pipeline),
    }
    resolved = resolve_config(
        ParamConfig(), validated(UserConfig, user, source="pipeline file run + params")
    )
    return resolved.model_copy(update={"run_id": run_id, "pipeline": _provenance(pipeline, path)})


def _provenance(pipeline: PipelineFile, path: Path) -> PipelineProvenance:
    return PipelineProvenance(
        name=path.stem, file=str(path), content=pipeline.model_dump(mode="json")
    )
