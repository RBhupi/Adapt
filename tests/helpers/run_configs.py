# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Run configs for tests, built the way ``adapt run`` builds them."""

from pathlib import Path
from typing import Any

from adapt.configuration.pipeline_file import PipelineFile
from adapt.configuration.pipeline_run import run_config
from adapt.configuration.schemas.internal import InternalConfig
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
