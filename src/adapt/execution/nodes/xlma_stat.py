# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""xlma_stat — lightning (xLMA) statistics on each scan's minute-resolution cell masks.

Lightning science only: reads xLMA flash-sorted NetCDF files (``*.nc``) from
``input_dir`` — raw LYLOUT ASCII must be converted in xLMA/pyxlma first
(pyxlma is not on conda and not a dependency); non-NetCDF files are ignored.
Each flash is matched to the cell mask of *its own minute*. The masks are the
ones the scan owns in its analysis dataset (produced upstream by projection and
tracking; this node never projects cells, interpolates masks, or estimates
motion). Two tables result:

- ``xlma_stat_minutes`` — the authoritative association, one row per
  ``(cell_uid, minute)``, with the mask's scan provenance. Every attributed
  flash is here.
- ``xlma_stat_scan`` — radar-aligned aggregate of the minutes a scan owns, one
  row per cell of that scan with lightning, derived only from the minute rows.
  Its key is the key of every per-cell table (run, scan, cell label), so the
  ``cells_by_scan`` view carries its ``xlma_*`` columns beside the cell's uid.
  The minutes before a scan hold the previous scan's cells: lightning of a cell
  that is no longer in the scan (its track ended or merged away) has no row
  here and stays in the minute rows.

It runs scan by scan like any module: beside a live run, or later over a stored
run (``run.source: stored_run``), where it reads each scan's analysis dataset
back from the store.
"""

import glob
import logging
import os

import pandas as pd

from adapt.contracts import (
    ProductTableWrite,
    SharedConfig,
    check_xlma_stat_minutes,
    check_xlma_stat_scan,
)
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from adapt.modules.xlma_stat.aggregate import cell_scan_rows
from adapt.modules.xlma_stat.attribution import UNATTRIBUTED
from adapt.modules.xlma_stat.config import XlmaStatConfig
from adapt.modules.xlma_stat.masks import scan_cell_labels, scan_minute_masks
from adapt.modules.xlma_stat.module import XlmaStatistics
from adapt.modules.xlma_stat.reader import read_flash_sorted_frames

logger = logging.getLogger(__name__)


class XlmaStatModule(BaseModule):
    name = "xlma_stat"
    summary = (
        "Lightning (xLMA) statistics on minute-projected cells; "
        "needs params: xlma_stat: {input_dir: ...}"
    )
    options_class = XlmaStatConfig
    inputs = ["xlma_stat_config", "analysis_ds", "scan_time"]
    outputs = ["xlma_stat_minutes_rows", "xlma_stat_scan_rows"]
    output_contracts = {
        "xlma_stat_minutes_rows": check_xlma_stat_minutes,
        "xlma_stat_scan_rows": check_xlma_stat_scan,
    }
    persistence = (
        ProductTableWrite(
            key="xlma_stat_minutes_rows",
            table="xlma_stat_minutes",
            primary_key=("run_id", "time", "cell_uid"),
            index_columns=("cell_uid", "time"),
        ),
        ProductTableWrite(
            key="xlma_stat_scan_rows",
            table="xlma_stat_scan",
            primary_key=("run_id", "scan_id", "cell_label"),
        ),
    )

    @classmethod
    def configure(cls, options: XlmaStatConfig, shared: SharedConfig) -> XlmaStatConfig:
        """The options; refuses a run without an existing ``input_dir`` folder."""
        if options.input_dir is None:
            raise ValueError(
                "xlma_stat needs the folder of xLMA flash-sorted NetCDF files; set it "
                "in the pipeline file as params: xlma_stat: {input_dir: ...}"
            )
        if not os.path.isdir(options.input_dir):
            raise ValueError(f"params.xlma_stat.input_dir '{options.input_dir}' is not a folder")
        if not any(name.endswith(".nc") for name in os.listdir(options.input_dir)):
            raise ValueError(
                f"params.xlma_stat.input_dir '{options.input_dir}' holds no flash-sorted NetCDF "
                "(*.nc) files; convert raw LYLOUT ASCII sources with xLMA/pyxlma first"
            )
        return options

    def __init__(self) -> None:
        # The folder's flashes, re-read only when its files change: every scan
        # reads the same files, and a live run sees new ones as they arrive.
        self._folder: tuple[tuple[str, int, int], ...] | None = None
        self._flashes: tuple[pd.DataFrame, pd.DataFrame] | None = None

    def run(self, context: dict) -> dict:
        config = context["xlma_stat_config"]
        analysis_ds = context["analysis_ds"]
        masks = scan_minute_masks(analysis_ds, context["scan_time"])
        flashes, sources = self._read(config.input_dir)
        radar_origin = (
            float(analysis_ds.attrs["radar_latitude"]),
            float(analysis_ds.attrs["radar_longitude"]),
        )
        minutes_df, by_uid = XlmaStatistics(config).compute(flashes, sources, masks, radar_origin)
        cells = scan_cell_labels(analysis_ds)
        scan_df = cell_scan_rows(by_uid, cells)
        ended = [uid for uid in by_uid["cell_uid"] if uid not in cells and uid != UNATTRIBUTED]
        logger.info(
            "xlma_stat: %d minute masks -> %d (cell, minute) rows, %d cell rows of this scan",
            len(masks),
            len(minutes_df),
            len(scan_df),
        )
        if ended:
            logger.info(
                "xlma_stat: %d cell(s) with lightning are no longer in this scan; their "
                "lightning is in xlma_stat_minutes only: %s",
                len(ended),
                ", ".join(ended),
            )
        return {
            "xlma_stat_minutes_rows": minutes_df,
            "xlma_stat_scan_rows": scan_df,
        }

    def _read(self, input_dir: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        files = sorted(glob.glob(os.path.join(input_dir, "*")))
        nc_files = [f for f in files if f.endswith(".nc")]
        folder = tuple((f, os.stat(f).st_mtime_ns, os.stat(f).st_size) for f in nc_files)
        if self._flashes is None or folder != self._folder:
            ignored = len(files) - len(nc_files)
            if ignored:
                logger.info("xlma_stat: ignoring %d non-NetCDF file(s) in %s", ignored, input_dir)
            self._flashes = read_flash_sorted_frames(nc_files)
            self._folder = folder
        return self._flashes


registry.register(XlmaStatModule)
