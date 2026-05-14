import pandas as pd
import pytest

pytestmark = pytest.mark.unit
from adapt.modules.analysis.module import RadarCellAnalyzer  # noqa: E402


def test_extract_single_cell(labeled_ds_with_extras, make_analysis_config):
    """Analyzer extracts single cell statistics."""
    config = make_analysis_config()
    analyzer = RadarCellAnalyzer(config)

    df = analyzer.extract(labeled_ds_with_extras)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["cell_label"] == 1


def test_extract_produces_required_columns(labeled_ds_with_extras, make_analysis_config):
    """Analyzer produces required output columns."""
    config = make_analysis_config()
    analyzer = RadarCellAnalyzer(config)

    df = analyzer.extract(labeled_ds_with_extras)
    row = df.iloc[0]

    assert "cell_area_sqkm" in row
    assert "area_40dbz_km2" in row
    assert "cell_centroid_geom_x" in row
    assert "cell_centroid_geom_y" in row
    assert "radar_reflectivity_max" in row
    assert "radar_differential_reflectivity_max" in row
