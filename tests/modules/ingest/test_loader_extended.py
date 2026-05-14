"""Extended tests for RadarDataLoader functionality."""

import pytest

from adapt.modules.ingest.module import RadarDataLoader

pytestmark = pytest.mark.unit


def test_loader_stores_grid_shape(make_ingest_config):
    """Loader stores grid_shape from config."""
    config = make_ingest_config()
    loader = RadarDataLoader(config)
    assert loader.grid_shape is not None
    assert len(loader.grid_shape) == 3


def test_loader_with_custom_grid_shape(make_ingest_config):
    """Loader respects custom grid_shape."""
    config = make_ingest_config(grid_shape=(10, 50, 50))
    loader = RadarDataLoader(config)
    assert loader.grid_shape == (10, 50, 50)


def test_loader_with_custom_weighting_function(make_ingest_config):
    """Loader respects weighting function config."""
    config = make_ingest_config(regridder={"weighting_function": "barnes"})
    loader = RadarDataLoader(config)
    assert loader.weighting_function == "barnes"


def test_loader_with_custom_min_radius(make_ingest_config):
    """Loader respects min_radius config."""
    config = make_ingest_config(regridder={"min_radius": 2000.0})
    loader = RadarDataLoader(config)
    assert loader.min_radius == 2000.0


def test_loader_with_custom_roi_func(make_ingest_config):
    """Loader respects roi_func config."""
    config = make_ingest_config(regridder={"roi_func": "dist"})
    loader = RadarDataLoader(config)
    assert loader.roi_func == "dist"


def test_loader_with_custom_grid_limits(make_ingest_config):
    """Loader respects custom grid_limits."""
    config = make_ingest_config(
        grid_limits=((0, 10000), (-50000, 50000), (-50000, 50000))
    )
    loader = RadarDataLoader(config)
    assert loader.grid_limits[0] == (0, 10000)


def test_loader_initialization_succeeds(make_ingest_config):
    """Loader can be created successfully."""
    config = make_ingest_config()
    loader = RadarDataLoader(config)
    assert loader is not None


def test_read_nonexistent_file_raises(tmp_path, make_ingest_config):
    """Reading non-existent file raises FileNotFoundError."""
    config = make_ingest_config()
    loader = RadarDataLoader(config)
    with pytest.raises(FileNotFoundError, match="Radar file not found"):
        loader.read(tmp_path / "nonexistent.nc")
