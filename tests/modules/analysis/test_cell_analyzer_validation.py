import pytest

pytestmark = pytest.mark.unit
from adapt.modules.analysis.module import RadarCellAnalyzer  # noqa: E402


def test_extract_requires_cell_labels(labeled_ds_with_extras, make_analysis_config):
    """Analyzer works correctly when cell_labels variable is present.
    
    NOTE: This replaces the old defensive check test. After SRP refactoring,
    the analyzer no longer validates input - it assumes the segmenter has
    already added labels. Input validation is Pydantic's responsibility.
    """
    config = make_analysis_config()
    analyzer = RadarCellAnalyzer(config)

    # With labels present, extract should work
    analyzer.extract(labeled_ds_with_extras)
