import pytest

from adapt.modules.analysis.module import RadarCellAnalyzer

pytestmark = pytest.mark.unit


def test_init_with_default_config(make_analysis_config):
    """Analyzer initializes with default config."""
    config = make_analysis_config()
    analyzer = RadarCellAnalyzer(config)

    assert analyzer.reflectivity_field == "reflectivity"
    assert analyzer.max_projection_steps > 0


def test_init_custom_config(make_analysis_config):
    """Analyzer initializes with custom config."""
    from adapt.configuration.schemas.user import UserProjectorConfig

    config = make_analysis_config(
        reflectivity_var="dbz", projector=UserProjectorConfig(max_projection_steps=2)
    )
    analyzer = RadarCellAnalyzer(config)

    assert analyzer.reflectivity_field == "dbz"
    assert analyzer.max_projection_steps == 2
