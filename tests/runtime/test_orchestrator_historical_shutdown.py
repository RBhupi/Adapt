import pytest

from adapt.runtime.orchestrator import PipelineOrchestrator

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]


class _FakeDownloader:
    def __init__(
        self, complete: bool, alive: bool, processed: int = 0, expected: int = 0
    ):
        self._complete = complete
        self._alive = alive
        self._processed = processed
        self._expected = expected
        self.stop_called = False

    def is_historical_complete(self) -> bool:
        return self._complete

    def is_alive(self) -> bool:
        return self._alive

    def get_historical_progress(self):
        return self._processed, self._expected

    def stop(self):
        self.stop_called = True

    def join(self, timeout=None):
        self._alive = False


class _FakeProcessor:
    def __init__(self):
        self._alive = True
        self.stop_called = False

    def is_alive(self) -> bool:
        return self._alive

    def stop(self):
        self.stop_called = True
        self._alive = False

    def join(self, timeout=None):
        self._alive = False


class _FakeRepository:
    def __init__(self):
        self.finalized = False
        self.closed = False

    def finalize_run(self, status: str):
        self.finalized = True

    def close(self):
        self.closed = True


def test_historical_complete_returns_true_and_stops_processor(pipeline_config):
    pipeline_config = pipeline_config.model_copy(update={"mode": "historical"})
    orch = PipelineOrchestrator(pipeline_config)
    orch.downloader = _FakeDownloader(
        complete=True, alive=False, processed=5, expected=5
    )
    orch.processor = _FakeProcessor()

    done = orch._check_historical_complete()

    assert done is True
    assert orch.downloader.stop_called is True
    assert orch.processor.stop_called is True


def test_historical_not_complete_returns_false_when_downloader_dead(pipeline_config):
    pipeline_config = pipeline_config.model_copy(update={"mode": "historical"})
    orch = PipelineOrchestrator(pipeline_config)
    orch.downloader = _FakeDownloader(complete=False, alive=False)

    done = orch._check_historical_complete()

    assert done is False


def test_stop_skips_repository_close_when_owned_externally(pipeline_config):
    orch = PipelineOrchestrator(pipeline_config, close_repository_on_stop=False)
    repo = _FakeRepository()
    orch.repository = repo

    orch.stop()

    assert repo.finalized is True
    assert repo.closed is False

    orch.close_repository()
    assert repo.closed is True
