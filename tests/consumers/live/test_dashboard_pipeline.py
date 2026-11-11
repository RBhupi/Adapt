# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The dashboard wizard's launch, without Tk: the store it makes, the
GUI-owned pipeline file it writes and the command that runs that file.
"""

import subprocess
from datetime import UTC, datetime

import pytest

from adapt.configuration.pipeline_file import load_pipeline_file
from adapt.configuration.schemas.errors import ConfigError
from adapt.consumers.live._utils import (
    adapt_cmd,
    init_store_cmd,
    is_repository,
    write_dashboard_pipeline,
)

pytestmark = pytest.mark.unit


def test_init_command_makes_the_folder_a_store(tmp_path):
    result = subprocess.run(  # noqa: S603 — fixed args, same interpreter
        init_store_cmd(tmp_path), capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert is_repository(tmp_path)


def test_wizard_run_values_go_into_the_dashboard_pipeline_file(tmp_path):
    write_dashboard_pipeline(
        tmp_path,
        radar="KTST",
        mode="historical",
        start_time="2025-06-17T18:00:00Z",
        end_time="2025-06-17T19:00:00Z",
    )

    run = load_pipeline_file(tmp_path / "dashboard.yaml").run
    assert (run.source, run.radar, run.mode, run.start_time, run.end_time) == (
        "aws_nexrad",
        "KTST",
        "historical",
        datetime(2025, 6, 17, 18, tzinfo=UTC),
        datetime(2025, 6, 17, 19, tzinfo=UTC),
    )


def test_dashboard_pipeline_runs_the_templates_modules(tmp_path):
    write_dashboard_pipeline(tmp_path, radar="KTST", mode="realtime")

    pipeline = load_pipeline_file(tmp_path / "dashboard.yaml")
    assert pipeline.modules == (
        "ingest",
        "detection",
        "projection",
        "analysis",
        "tracking",
        "cell_volume_stats",
    )


def test_dashboard_pipeline_is_run_by_name_from_the_store_root(tmp_path):
    cmd = write_dashboard_pipeline(tmp_path, radar="KTST", mode="realtime")

    # `adapt run NAME` reads ./NAME.yaml, so the command runs with cwd = the root.
    assert cmd == [*adapt_cmd(), "run", "dashboard"]
    assert (tmp_path / "dashboard.yaml").is_file()


def test_invalid_wizard_values_are_refused_before_anything_is_written(tmp_path):
    with pytest.raises(ConfigError, match="start_time"):
        write_dashboard_pipeline(
            tmp_path, radar="KTST", mode="historical", start_time="2025-06-17 18:00"
        )

    assert list(tmp_path.iterdir()) == []
