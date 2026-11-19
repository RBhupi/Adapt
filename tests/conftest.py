"""Root-level pytest fixtures for Adapt test suite.

Provides the shared configuration fixtures. A run config is built the way
``adapt run`` builds it, from a pipeline file held in memory
(``tests.helpers.run_configs``); a module's config is built by the module's own
``configure`` from its options model.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from adapt.execution.nodes.analysis import AnalysisModule
from adapt.execution.nodes.detection import DetectModule
from adapt.execution.nodes.ingest import LoadModule
from adapt.execution.nodes.projection import ProjectionModule
from adapt.execution.nodes.tracking import TrackingModule
from adapt.modules.analysis.config import AnalysisOptions
from adapt.modules.detection.config import DetectionOptions
from adapt.modules.ingest.config import IngestOptions
from adapt.modules.projection.config import ProjectionOptions
from adapt.modules.tracking.config import TrackingOptions
from adapt.persistence.store import init_store
from adapt.persistence.store_registry import StoreRegistry
from tests.helpers.run_configs import new_run_config
from tests.helpers.shared_config import SHARED

# =============================================================================
# Resource isolation
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_repository_registry():
    """Release the process-global registry cache after every test.

    ``StoreRegistry.get_instance`` caches one WAL connection per store root for
    the process lifetime. Each test uses a fresh root, so without this reset
    the cache accumulates open descriptors across the suite and exhausts a low
    ``ulimit -n`` (macOS default 256). Closing per test keeps descriptors flat
    and isolates registry state between tests.
    """
    yield
    StoreRegistry.close_all()


# =============================================================================
# Directory Fixtures
# =============================================================================


@pytest.fixture
def temp_dir():
    """Temporary directory that is cleaned up after test."""
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def store_root(temp_dir):
    """An initialized store root (registry.db + logs/ + collections/).

    The registry connection is released here, before ``temp_dir`` removes the
    directory: Windows refuses to delete ``registry.db`` while it is open, and
    the autouse reset above only tears down after ``temp_dir``.
    """
    root = init_store(temp_dir / "store")
    yield root
    StoreRegistry.close_all()


# =============================================================================
# Run Config Fixtures
# =============================================================================


@pytest.fixture
def internal_config(store_root):
    """The config of a new run of a pipeline file that sets nothing.

    Use this when a test does not care about specific config values and just
    needs the config a run has: a realtime ``TEST_RADAR`` run of the six
    modules of a plain NEXRAD run, in this test's store.
    """
    return new_run_config(store_root)


@pytest.fixture
def make_config(store_root):
    """Factory for the config of a new run of a pipeline file with given sections.

    Examples
    --------
    >>> def test_custom_level(make_config):
    ...     config = make_config(params={"global": {"z_level": 3000}})
    ...     assert config.global_.z_level == 3000.0
    """

    def _make(**sections):
        """``new_run_config`` in this test's store: ``run``, ``modules``, ``params``."""
        return new_run_config(store_root, **sections)

    return _make


# =============================================================================
# Per-Module Config Fixtures
# =============================================================================


@pytest.fixture
def detection_module_config(make_detection_config):
    # Threshold segmenter (the default): the pyart methods need a 3D grid,
    # which these unit fixtures don't produce.
    return make_detection_config()


@pytest.fixture
def analysis_module_config(make_analysis_config):
    return make_analysis_config()


@pytest.fixture
def projection_module_config(make_projection_config):
    return make_projection_config()


@pytest.fixture
def tracking_module_config(make_tracking_config):
    return make_tracking_config()


@pytest.fixture
def ingest_module_config(make_ingest_config):
    return make_ingest_config()


@pytest.fixture
def make_detection_config():
    def _make(threshold=None, **options):
        """Detection's config, as a run with default shared values configures it, for
        ``options`` (``params.detection``); ``threshold`` is short for the threshold
        method's own parameter."""
        if threshold is not None:
            options["threshold_params"] = {"threshold": threshold}
        return DetectModule.configure(DetectionOptions(**options), SHARED)

    return _make


@pytest.fixture
def make_analysis_config():
    def _make(**options):
        """Analysis's config, as a run with default shared values configures it,
        for ``options`` (``params.analysis``)."""
        return AnalysisModule.configure(AnalysisOptions(**options), SHARED)

    return _make


@pytest.fixture
def make_projection_config():
    def _make(**options):
        """Projection's config, as a run with default shared values configures it,
        for ``options`` (``params.projection``)."""
        return ProjectionModule.configure(ProjectionOptions(**options), SHARED)

    return _make


@pytest.fixture
def make_tracking_config():
    def _make(**options):
        """Tracking's config, as a run with default shared values configures it,
        for ``options`` (``params.tracking``)."""
        return TrackingModule.configure(TrackingOptions(**options), SHARED)

    return _make


@pytest.fixture
def make_ingest_config():
    def _make(**options):
        """Ingest's config, as a run with default shared values configures it,
        for ``options`` (``params.ingest``)."""
        return LoadModule.configure(IngestOptions(**options), SHARED)

    return _make
