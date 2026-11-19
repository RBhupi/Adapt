# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""A run's config from a pipeline file: ``run_config``.

``run_config`` turns a loaded file into the run's ``InternalConfig``: the run
section, the ``global``, ``downloader`` and ``logging`` settings (their defaults
with the file's laid over), every listed module's validated options, and the
provenance record of the file. It generates the run id, and reloads a stored
run's config for ``--run-id``. It configures every module the run will run and
builds their graph, so a module that refuses its options or lacks an input
fails before anything starts.
"""

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from adapt.configuration.pipeline_file import (
    PipelineFile,
    param_section_models,
    pipeline_source,
)
from adapt.configuration.schemas.errors import ConfigError, OptionsRefused, together, validated
from adapt.configuration.schemas.internal import InternalConfig, PipelineProvenance
from adapt.configuration.schemas.module_resolver import (
    module_options,
    recorded_options,
    resolve_module_configs,
)
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
    and, in ``module_params``, the validated options of every listed module,
    complete: its defaults with the file's laid over.

    ``run.source: stored_run`` runs the file's modules over the stored run
    ``run_id``: that run's recorded config, with the file's module list and
    those modules' own options (their defaults with the file's laid over, in
    place of what the stored run recorded for them). A module the stored run
    ran must get the options the run recorded for it, unless it keys its rows
    by its options (``BaseModule.rows_keyed_by_options``).

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
        still running, was recorded with a config this Adapt version cannot
        read, or ran a listed module with other options; a historical run has
        no ``run.start_time`` or ``run.end_time``; or the file's settings do
        not configure the run and every module (a wrong option is named as
        ``params.<section>.<option>`` or ``params.<module>.<option>``).
    """
    if pipeline.run.source == _STORED_SOURCE:
        config = _stored_run_config(pipeline, path, run_id)
    elif run_id is None:
        config = _internal_config(pipeline, path, _new_run_id(pipeline, path))
    else:
        config = _named_run_config(pipeline, path, run_id)
    _configure(_told_by(pipeline, path), config)
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
    return _configure(_told_by(pipeline, path), _internal_config(pipeline, path, run_id=""))


def _told_by(pipeline: PipelineFile, path: Path) -> str:
    """How a refusal begins: the file, and the command line's settings when it gave any."""
    source = pipeline_source(path, pipeline.overrides)
    return source[:1].upper() + source[1:]


def _configure(told_by: str, config: InternalConfig) -> list:
    """Build every listed module's config from ``config`` and wire their graph.
    ``told_by`` names where the settings came from, for a refusal."""
    try:
        enabled = resolve_enabled_modules(registry.create_modules(), config.modules)
        configs = resolve_module_configs(config, [m.name for m in enabled])
        scan_graph(enabled, configs, stored_run=config.source == _STORED_SOURCE)
    except ValueError as exc:  # a module refused (pydantic's ValidationError is one)
        raise ConfigError(f"{told_by}: {exc}") from exc
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
    sections = [key for key in pipeline.params if key in param_section_models()]
    if sections:
        raise ConfigError(
            f"{_told_by(pipeline, path)}: {', '.join(f'params.{s}' for s in sections)} cannot "
            f"change over a stored run, which keeps the settings it was made with — "
            "remove them (the options of the listed modules still apply)"
        )
    given, refused = _module_params(pipeline, path)
    if refused:
        raise together(refused)
    _refuse_other_options(recorded, given, path)
    print(f"Running {', '.join(pipeline.modules)} over stored run {run_id}")
    return recorded.model_copy(
        update={
            "source": _STORED_SOURCE,
            "mode": "historical",
            "modules": list(pipeline.modules),
            "module_params": {**recorded.module_params, **given},
            "pipeline": _provenance(pipeline, path),
        }
    )


def _refuse_other_options(
    recorded: InternalConfig, given: Mapping[str, dict[str, Any]], path: Path
) -> None:
    """Refuse a pass over stored run ``recorded`` that gives a module the run ran
    (``given``: the pass's options, by module) other options than the run
    recorded for it.

    The pass writes the module's rows over the run's key by key, so the rows
    only the recorded options made would stay among the new ones. A module that
    keys its rows by its options (``rows_keyed_by_options``) is exempt.
    """
    differing: dict[str, list[str]] = {}
    for name, options in given.items():
        if name not in recorded.module_params or registry.get(name).rows_keyed_by_options:
            continue
        ran_with = recorded_options(recorded, name)
        assert ran_with is not None  # the pass gives it options, so it has options
        lines = [
            f"  • {at} — recorded: {json.dumps(was)}; this file: {json.dumps(now)}"
            for at, was, now in _differences(_in_json(ran_with), options, f"params.{name}")
        ]
        if lines:
            differing[name] = lines
    if differing:
        names = ", ".join(differing)
        raise ConfigError(
            f"Pipeline file {path}: run {recorded.run_id} ran {names} with other options "
            "than this file gives:\n"
            + "\n".join(line for lines in differing.values() for line in lines)
            + f"\nThe rows of {names} are not keyed by options, so this pass would write "
            "over some of the run's rows and leave the others: rows of both options, mixed. "
            f"Give {names} the recorded options (the recorded values above), or start a new "
            "run with the new ones."
        )


def _differences(recorded: Any, given: Any, at: str) -> list[tuple[str, Any, Any]]:
    """``(option, recorded value, given value)`` for each option under ``at`` whose
    two values differ; a part with the same keys in both is told key by key."""
    if (
        isinstance(recorded, Mapping)
        and isinstance(given, Mapping)
        and recorded.keys() == given.keys()
    ):
        return [
            difference
            for key in recorded
            for difference in _differences(recorded[key], given[key], f"{at}.{key}")
        ]
    return [] if recorded == given else [(at, recorded, given)]


def _in_json(options: BaseModel) -> dict[str, Any]:
    """A module's validated options, complete, as a run record's JSON holds them."""
    record: dict[str, Any] = json.loads(options.model_dump_json())
    return record


def _module_params(
    pipeline: PipelineFile, path: Path
) -> tuple[dict[str, dict[str, Any]], list[OptionsRefused]]:
    """What a run records of every listed module (the file gives params to
    listed modules only), and the refusal of each module whose options are
    wrong: every one is checked, so all can be told together."""
    recorded: dict[str, dict[str, Any]] = {}
    refused: list[OptionsRefused] = []
    source = pipeline_source(path, pipeline.overrides)
    for name in pipeline.modules:
        try:
            record = _options_record(name, pipeline.params.get(name, {}), source)
        except OptionsRefused as exc:
            refused.append(exc)
            continue
        if record is not None:
            recorded[name] = record
    return recorded, refused


def _options_record(name: str, given: Mapping[str, Any], source: str) -> dict[str, Any] | None:
    """What a run records of module ``name``, given these options by ``source``
    (the file, and the command line's settings): its validated options,
    complete, as the run record's JSON holds them; None for a module without
    options. The run is configured from this record, now and whenever it is
    resumed, so both get the same options.

    Raises
    ------
    OptionsRefused
        An option is wrong, or holds a value the record would not give back:
        JSON holds no infinity or NaN.
    """
    in_json = f"{source}, as the run records it — JSON holds no infinity or NaN"
    options = module_options(name, given, source=source)
    if options is None:
        return None
    record = _in_json(options)
    # The run is configured from the record, so what the record does not give
    # back is refused here, where the file can be named.
    read_back = module_options(name, record, source=in_json)
    assert read_back is not None  # the module has options: they were just validated
    lost = _differences(read_back.model_dump(), options.model_dump(), f"params.{name}")
    if lost:
        raise OptionsRefused(
            in_json,
            [
                f"  • {at} — value {now!r} would be read back as {json.dumps(was)}; "
                "give a finite number, or leave the option empty if it may be"
                for at, was, now in lost
            ],
        )
    return record


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
    if pipeline.overrides:
        raise ConfigError(
            f"Run {run_id} is resumed with the options it recorded, so --set cannot change "
            f"them (given: {', '.join(pipeline.overrides)}). Leave --set out to resume it, "
            "or start a new run with these options"
        )
    print(f"Continuing existing run ID: {run_id}")
    print(
        "It keeps its recorded modules and params; of the pipeline file only "
        "run.max_runtime_minutes applies."
    )
    return recorded


def _recorded_config(run: Mapping[str, Any], path: Path) -> InternalConfig:
    """The config a stored run was made with, in the store as it is now: the
    file's folder, wherever the store was when the run was recorded.

    Raises
    ------
    ConfigError
        This Adapt version cannot read the recorded config.
    """
    try:
        recorded = InternalConfig.model_validate_json(run["config_json"])
    except ValidationError as exc:
        keys = dict.fromkeys(".".join(map(str, error["loc"])) for error in exc.errors())
        raise ConfigError(
            f"Run {run['run_id']}: this Adapt version cannot read its recorded config "
            f"(runs.config_json), at: {', '.join(keys)}. The run was recorded with "
            f"pipeline_version {run['pipeline_version']}. It cannot be resumed or used for a "
            "stored-run pass (run.source: stored_run) with this version"
        ) from exc
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
    """The config of new run ``run_id`` of the file: its run section, the
    ``global``, ``downloader`` and ``logging`` settings (their defaults with the
    file's laid over) and every listed module's options.

    Raises
    ------
    ConfigError
        Options are wrong: every wrong one of the listed modules and of the
        three sections is told, each as ``params.<key>.<option>``, in one
        message that names the file. Or a historical run that fetches or reads
        scans has no time window.
    """
    run = pipeline.run
    params = pipeline.params
    module_params, refused = _module_params(pipeline, path)
    try:
        config = validated(
            InternalConfig,
            {
                "mode": run.mode,
                "base_dir": str(path.parent),
                "source": run.source,
                "source_dir": run.source_dir,
                "run_id": run_id,
                "downloader": {
                    **params.get("downloader", {}),
                    "mode": run.mode,
                    "radar": run.radar,
                    "start_time": None if run.start_time is None else to_scan_iso(run.start_time),
                    "end_time": None if run.end_time is None else to_scan_iso(run.end_time),
                },
                "global": params.get("global", {}),
                "logging": params.get("logging", {}),
                "module_params": module_params,
                "modules": list(pipeline.modules),
                "pipeline": _provenance(pipeline, path),
            },
            source=pipeline_source(path, pipeline.overrides),
            at=("params",),
        )
    except OptionsRefused as exc:
        raise together([*refused, exc]) from exc
    if refused:
        raise together(refused)
    # A pass over a stored run takes its scans from that run, not from a window.
    if run.mode == "historical" and run.source != _STORED_SOURCE:
        for key in ("start_time", "end_time"):
            if getattr(run, key) is None:
                raise ConfigError(
                    f"{key} is required when mode='historical'. Set run.{key} in the pipeline file."
                )
    return config


def _provenance(pipeline: PipelineFile, path: Path) -> PipelineProvenance:
    return PipelineProvenance(
        name=path.stem, file=str(path), content=pipeline.model_dump(mode="json")
    )
