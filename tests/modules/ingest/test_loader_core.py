import pytest

pytestmark = pytest.mark.unit
from adapt.modules.ingest.module import RadarDataLoader  # noqa: E402

# Note: Legacy tests for None/incomplete dict configs removed.
# InternalConfig validation now prevents invalid configurations at creation time.


def test_read_missing_file_raises(ingest_module_config_from_radar):
    """Loader raises FileNotFoundError for missing files."""
    loader = RadarDataLoader(ingest_module_config_from_radar)
    with pytest.raises(FileNotFoundError, match="Radar file not found"):
        loader.read("/does/not/exist")


def test_regrid_propagates_exception(monkeypatch, ingest_module_config_from_radar):
    """Loader propagates regridding exceptions to the caller."""
    loader = RadarDataLoader(ingest_module_config_from_radar)

    def boom(*a, **k):
        raise RuntimeError("fail")

    monkeypatch.setattr("pyart.map.grid_from_radars", boom)

    with pytest.raises(RuntimeError, match="fail"):
        loader.regrid(object())
