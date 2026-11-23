# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The options of the run's source, set under ``params.<source name>``.

A run of a file records its source's validated options, complete, under
``source_params``. The options of a source the file does not run are kept in
the file and never read. A pass over a stored run has no source options.
"""

import json

import pytest

from adapt.configuration.pipeline_file import PipelineFile
from adapt.configuration.pipeline_run import configured_modules, run_config
from adapt.configuration.schemas.errors import ConfigError
from adapt.persistence.store import init_store
from adapt.persistence.store_registry import StoreRegistry
from tests.helpers.pipeline_files import MODULES
from tests.helpers.run_configs import record_run

pytestmark = pytest.mark.unit

_REALTIME = {"source": "aws_nexrad", "radar": "KBOX", "mode": "realtime"}
_STORED = {"source": "stored_run", "radar": "KBOX", "mode": "historical"}
_RUN_ID = "2026MAR23-0206-KBOX"


@pytest.fixture
def path(tmp_path):
    """A pipeline file's place: in a store root."""
    return init_store(tmp_path / "store") / "case.yaml"


def _pipeline(run=None, modules=MODULES, **params) -> PipelineFile:
    return PipelineFile.model_validate(
        {"run": run or _REALTIME, "modules": list(modules), "params": params}
    )


def _refusal(path, pipeline: PipelineFile) -> list[str]:
    with pytest.raises(ConfigError) as exc:
        run_config(pipeline, path)
    return str(exc.value).splitlines()


def test_a_file_that_sets_no_source_option_records_the_sources_defaults(path):
    assert run_config(_pipeline(), path).source_params == {
        "latest_files": 5,
        "latest_minutes": 60,
        "first_check_sec": 200,
        "expected_arrival_sec": 300,
        "late_after_sec": 450,
        "max_skipped_scans": 3,
        "max_fetch_retries": 3,
        "min_file_size": 1024,
    }


def test_a_source_option_of_the_file_reaches_the_run(path):
    config = run_config(_pipeline(aws_nexrad={"max_skipped_scans": 5}), path)

    assert config.source_params is not None
    assert config.source_params["max_skipped_scans"] == 5


def test_a_wrong_source_option_is_refused_naming_the_file_and_the_option(path):
    assert _refusal(path, _pipeline(aws_nexrad={"latest_files": 0})) == [
        f"Invalid configuration (pipeline file {path}):",
        "  • params.aws_nexrad.latest_files — value 0 is out of range — "
        "Input should be greater than or equal to 1",
        "The options of aws_nexrad: adapt config --params aws_nexrad",
    ]


def test_the_options_of_a_source_the_file_does_not_run_are_kept_unread(path):
    pipeline = _pipeline(local_directory={"min_file_size": 2048})

    config = run_config(pipeline, path)

    assert pipeline.params["local_directory"] == {"min_file_size": 2048}
    assert config.source_params is not None
    assert config.source_params["min_file_size"] == 1024


def _stored(path, **params) -> StoreRegistry:
    """Record run ``_RUN_ID`` of an ``aws_nexrad`` file with these params,
    completed; the store's registry."""
    record_run(path.parent, run_config(_pipeline(**params), path, run_id=_RUN_ID))
    registry = StoreRegistry.get_instance(path.parent)
    registry.finalize_run(_RUN_ID, "completed", scans_processed=0)
    return registry


def test_a_source_without_options_refuses_any_option(path):
    pipeline = _pipeline(_STORED, modules=["target_selection"], stored_run={"depth": 1})

    with pytest.raises(ConfigError) as exc:
        configured_modules(pipeline, path)

    assert "  • params.stored_run.depth — unrecognized option" in str(exc.value)


def test_a_pass_over_a_stored_run_refuses_an_option_of_stored_run(path):
    _stored(path)
    pipeline = _pipeline(_STORED, modules=["target_selection"], stored_run={"depth": 1})

    with pytest.raises(ConfigError) as exc:
        run_config(pipeline, path, run_id=_RUN_ID)

    assert "  • params.stored_run.depth — unrecognized option" in str(exc.value)


def test_a_pass_over_a_stored_run_has_no_source_options_and_leaves_the_record(path):
    registry = _stored(path, aws_nexrad={"max_skipped_scans": 5})

    config = run_config(_pipeline(_STORED, modules=["target_selection"]), path, run_id=_RUN_ID)

    assert config.source_params is None
    kept = json.loads(registry.get_run(_RUN_ID)["config_json"])
    assert kept["source_params"]["max_skipped_scans"] == 5
