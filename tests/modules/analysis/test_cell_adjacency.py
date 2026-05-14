import dataclasses
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from adapt.configuration.schemas.materialization import materialize_module_configs
from adapt.configuration.schemas.param import ParamConfig
from adapt.configuration.schemas.resolve import resolve_config
from adapt.configuration.schemas.user import UserConfig
from adapt.modules.analysis.module import RadarCellAnalyzer


@pytest.fixture
def config():
    d = tempfile.mkdtemp()
    try:
        import shutil

        param = ParamConfig()
        user = UserConfig(base_dir=str(Path(d)), radar="TEST_RADAR")
        internal = resolve_config(param, user, None)
        return materialize_module_configs(internal)["analysis_config"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _ds_with_labels(time, labels: np.ndarray) -> xr.Dataset:
    H, W = labels.shape
    ds = xr.Dataset(
        {
            "cell_labels": (["y", "x"], labels.astype(np.int32)),
            "reflectivity": (["y", "x"], np.zeros((H, W), dtype=np.float32)),
        },
        coords={
            "y": np.arange(H) * 1000.0,
            "x": np.arange(W) * 1000.0,
        },
    )
    return ds.assign_coords(time=time)


def test_extract_adjacency_simple_touch(config):
    analyzer = RadarCellAnalyzer(config)

    labels = np.zeros((4, 4), dtype=np.int32)
    labels[:, :2] = 1
    labels[:, 2:] = 2

    ds = _ds_with_labels(np.datetime64("2024-01-01T00:00:00"), labels)
    df = analyzer.extract_adjacency(ds)

    assert list(df.columns) == [
        "time",
        "cell_label_a",
        "cell_label_b",
        "touching_boundary_pixels",
    ]
    assert len(df) == 1
    assert int(df.iloc[0]["cell_label_a"]) == 1
    assert int(df.iloc[0]["cell_label_b"]) == 2
    # boundary between col=1 and col=2 has 4 touching edges (one per row)
    assert int(df.iloc[0]["touching_boundary_pixels"]) == 4


def test_extract_adjacency_threshold_filters(config):
    # Override threshold to require >4 touches so pair is filtered out
    cfg = dataclasses.replace(config, adjacency_min_touching=5)
    analyzer = RadarCellAnalyzer(cfg)

    labels = np.zeros((4, 4), dtype=np.int32)
    labels[:, :2] = 1
    labels[:, 2:] = 2

    ds = _ds_with_labels(np.datetime64("2024-01-01T00:00:00"), labels)
    df = analyzer.extract_adjacency(ds)

    assert isinstance(df, pd.DataFrame)
    assert df.empty
