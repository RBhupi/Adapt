# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Run configs for tests, built the way ``adapt run`` builds them."""

from dataclasses import replace
from pathlib import Path
from typing import Any

from adapt.configuration.pipeline_file import PipelineFile
from adapt.configuration.pipeline_run import run_config
from adapt.configuration.schemas.internal import InternalConfig
from adapt.persistence.store_registry import RunStart, StoreRegistry
from adapt.runtime.provenance import module_provenance
from tests.helpers.pipeline_files import MODULES

RADAR = "TEST_RADAR"


def new_run_config(root: Path, run_id: str | None = None, **sections: Any) -> InternalConfig:
    """The config of a new run, in store ``root``, of a pipeline file with these sections.

    ``sections`` are the file's ``run``, ``modules`` and ``params``. Unless
    given: a realtime ``aws_nexrad`` run of radar ``RADAR`` (``run`` replaces
    single keys of that), the six modules of a plain NEXRAD run, and no params.
    The file is held in memory. The run gets the id a run started now gets, or
    ``run_id`` (``adapt run --run-id``: ``YYYYMONDD-HHMM-RADAR``).
    """
    run = {"source": "aws_nexrad", "radar": RADAR, "mode": "realtime", **sections.pop("run", {})}
    pipeline = PipelineFile.model_validate(
        {
            "run": run,
            "modules": list(sections.pop("modules", MODULES)),
            "params": dict(sections.pop("params", {})),
        }
    )
    if sections:
        raise TypeError(f"A pipeline file has no section {', '.join(sections)}")
    return run_config(pipeline, root / "test.yaml", run_id)


def record_run(root: Path, config: InternalConfig, **changed: Any) -> None:
    """Record ``config``'s run in store ``root`` as the orchestrator does when a
    run starts: its collection, the run with its config, and what each of its
    modules runs with (``changed``: parts of the run's row that differ)."""
    registry = StoreRegistry.get_instance(root)
    registry.register_collection(config.downloader.radar, source_kind="aws_nexrad")
    start = RunStart(
        run_id=config.run_id,
        collection_id=config.downloader.radar,
        config_hash="h",
        config_json=config.model_dump_json(),
        pipeline_version="0",
        environment_json="{}",
    )
    registry.begin_run(replace(start, **changed))
    registry.record_module_provenance(config.run_id, module_provenance(config))
