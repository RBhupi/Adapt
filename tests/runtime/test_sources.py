# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The run's source: checked before the run is recorded, then built for it.

The runtime hands a source the run's values and its own options, and nothing
else of the run config. Which sources exist and how they are registered is
tested in ``tests/execution/test_source_registration.py``.
"""

import re

import pytest
from pydantic import BaseModel, ConfigDict

from adapt.contracts.source import SourceRun
from adapt.execution.source_registry import source_registry
from tests.helpers.run_configs import new_run_config

pytestmark = pytest.mark.unit


class _StandInOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    poll_interval_sec: int = 300
    min_file_size: int = 1024


class _StandInSource:
    """A source registered for these tests; it remembers what it was handed."""

    source_name = "stand_in"
    summary = "a source of no scans"
    options_class = _StandInOptions
    checked_with: tuple = ()

    def __init__(self, run, options, queue, acquire) -> None:
        self.built_with = (run, options, queue, acquire)

    @classmethod
    def check(cls, run, options) -> None:
        cls.checked_with = (run, options)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        pass

    def is_historical_complete(self) -> bool:
        return True

    def get_historical_progress(self) -> tuple[int, int]:
        return 0, 0


_RUN = {
    "radar": "KLOT",
    "mode": "historical",
    "start_time": "2024-01-01T12:00:00Z",
    "end_time": "2024-01-01T13:00:00Z",
}


@pytest.fixture
def stand_in_config(store_root):
    """The config of a historical KLOT run whose source is the stand-in and whose
    file sets ``poll_interval_sec: 30``."""
    source_registry.register(_StandInSource)
    config = new_run_config(store_root, run=_RUN, params={"downloader": {"poll_interval_sec": 30}})
    yield config.model_copy(update={"source": "stand_in", "source_dir": "/data/klot"})
    source_registry.unregister("stand_in")


_HANDED = SourceRun(
    radar="KLOT",
    mode="historical",
    start_time="2024-01-01T12:00:00Z",
    end_time="2024-01-01T13:00:00Z",
    source_dir="/data/klot",
)


class TestBuildSource:
    def test_the_source_is_handed_the_runs_values_and_its_own_options(self, stand_in_config):
        from adapt.runtime.sources import build_source

        queue, acquire = object(), object()

        source = build_source(stand_in_config, queue, acquire)

        assert source.built_with == (
            _HANDED,
            _StandInOptions(poll_interval_sec=30, min_file_size=1024),
            queue,
            acquire,
        )

    def test_a_source_without_options_is_handed_none(self, stand_in_config):
        from adapt.runtime.sources import build_source

        plain = type("_Plain", (_StandInSource,), {"source_name": "plain", "options_class": None})
        source_registry.register(plain)
        try:
            source = build_source(
                stand_in_config.model_copy(update={"source": "plain"}), object(), object()
            )
        finally:
            source_registry.unregister("plain")

        assert source.built_with[0] == _HANDED
        assert source.built_with[1] is None


class TestCheckSource:
    def test_check_is_handed_the_runs_values_and_its_own_options(self, stand_in_config):
        from adapt.runtime.sources import check_source

        check_source(stand_in_config)

        assert _StandInSource.checked_with == (
            _HANDED,
            _StandInOptions(poll_interval_sec=30, min_file_size=1024),
        )

    def test_a_folder_that_does_not_exist_is_refused_keeping_the_source_text(
        self, store_root, tmp_path
    ):
        """The cli checks the run's source before the run starts; the source's own
        refusal reaches the user as a configuration error, word for word."""
        from adapt.configuration.schemas.errors import ConfigError
        from adapt.runtime.sources import check_source

        absent = tmp_path / "absent"
        run = {"source": "local_directory", **_RUN, "source_dir": str(absent)}

        refusal = re.escape(f"run.source_dir {absent} is not a folder")
        with pytest.raises(ConfigError, match=refusal):
            check_source(new_run_config(store_root, run=run))
