from dataclasses import replace

import pytest

from adapt.modules.analysis.config import AnalysisOptions
from adapt.modules.analysis.module import RadarCellAnalyzer
from adapt.modules.analysis.node import AnalysisModule
from tests.helpers.shared_config import SHARED

pytestmark = pytest.mark.unit


def test_init_with_default_config(make_analysis_config):
    """Analyzer initializes with default config."""
    config = make_analysis_config()
    analyzer = RadarCellAnalyzer(config)

    assert analyzer.reflectivity_field == "reflectivity"


def test_init_custom_config():
    """Analyzer initializes with a custom tracking field (the role knob).

    Note: what a file calls a field is a NAME mapping handled at ingest
    (ingest.field_map); selecting a different analysis field is
    global.tracking_field, and analysis.radar_variables must include it.
    """
    config = AnalysisModule.configure(
        AnalysisOptions(radar_variables=["dbz"]), replace(SHARED, tracking_field="dbz")
    )
    analyzer = RadarCellAnalyzer(config)

    assert analyzer.reflectivity_field == "dbz"
