"""Tests for setup_directories utility functions."""

from datetime import UTC, datetime

from adapt.configuration.schemas.directories import (
    get_plot_path,
    setup_output_directories,
)


def test_setup_output_directories_with_explicit_path(tmp_path):
    dirs = setup_output_directories(tmp_path)

    assert dirs["base"] == tmp_path
    assert dirs["logs"] == tmp_path / "logs"

    for key, path in dirs.items():
        assert path.exists(), f"{key} directory not created"


def test_setup_output_directories_creates_subdirs(tmp_path):
    setup_output_directories(tmp_path)

    assert tmp_path.is_dir()
    assert (tmp_path / "logs").is_dir()

    # Type-specific dirs are created dynamically under RADAR_ID/, not at root
    assert not (tmp_path / "nexrad").exists()
    assert not (tmp_path / "gridnc").exists()
    assert not (tmp_path / "analysis").exists()
    assert not (tmp_path / "plots").exists()


def test_setup_directories_expands_tilde(tmp_path):
    dirs = setup_output_directories(tmp_path)
    assert "~" not in str(dirs["base"])


def test_setup_directories_resolves_relative_paths(tmp_path):
    dirs = setup_output_directories(tmp_path)
    assert dirs["base"].is_absolute()


def test_get_plot_path_reflectivity(tmp_path):
    dirs = setup_output_directories(tmp_path)
    scan_time = datetime(2024, 1, 15, 12, 30, tzinfo=UTC)

    plot_path = get_plot_path(
        dirs, radar="KMOB", plot_type="reflectivity", scan_time=scan_time
    )

    assert plot_path is not None
    assert "20240115" in str(plot_path)
    assert "KMOB" in str(plot_path)


def test_get_plot_path_cells(tmp_path):
    dirs = setup_output_directories(tmp_path)
    scan_time = datetime(2024, 1, 15, 12, 30, tzinfo=UTC)

    plot_path = get_plot_path(
        dirs, radar="KMOB", plot_type="cells", scan_time=scan_time
    )

    assert plot_path is not None
    assert "KMOB" in str(plot_path)
