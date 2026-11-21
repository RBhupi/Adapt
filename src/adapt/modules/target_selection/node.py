# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""target_selection — after every tracked scan, choose the cell the radar should watch.

The rule (entry gates, score, hold gates, switching, duration cap) decides from
the run's cells up to this scan and the run's tracks: the module's two declared
store reads, ``cells_so_far`` and ``tracks_so_far``, made once the listed
modules that write those rows have stored this scan. The decision is the
``selection`` output, one row per scan and rule in the ``target_selection``
table; ``arm_radar_comm`` acts on it.
"""

from dataclasses import dataclass
from importlib.resources import files

from adapt.contracts import (
    ProductTableWrite,
    SharedConfig,
    StoreRead,
    check_module_state,
    check_selection,
)
from adapt.modules.base import BaseModule
from adapt.modules.target_selection import (
    CurrentTarget,
    TargetSelectionEngine,
    TSEConfig,
    config_hash,
    snapshot_at,
)
from adapt.modules.target_selection.table import selection_frame

_PRESET = files("adapt.modules.target_selection") / "presets" / "growing_isolated.yaml"


@dataclass(frozen=True)
class TargetSelectionRun:
    """The rule, and the field the run tracks (its per-cell max is ``field_max``)."""

    rules: TSEConfig
    tracking_field: str


@dataclass(frozen=True)
class SelectionState:
    """What target selection carries from one scan to the next: the target it holds."""

    current: CurrentTarget | None = None


class TargetSelectionModule(BaseModule):
    """Decide the target at every scan that has tracked cells."""

    name = "target_selection"
    summary = "choose the cell the radar should watch"
    # cells_so_far, tracks_so_far: the declared store reads below. cell_identity:
    # the module runs after tracking; over a stored run, a scan without stored
    # cells skips it.
    inputs = [
        "target_selection_config",
        "cell_identity",
        "cells_so_far",
        "tracks_so_far",
        "scan_time",
        "target_selection_state",
    ]
    outputs = ["selection", "target_selection_state"]
    store_reads = (
        StoreRead(key="cells_so_far", source="cells_by_scan", up_to_scan=True),
        StoreRead(key="tracks_so_far", source="cell_tracks", up_to_scan=False),
    )
    options_class = TSEConfig
    rows_keyed_by_options = True  # config_hash is in the table's key
    output_contracts = {
        "selection": check_selection,
        "target_selection_state": check_module_state,
    }
    persistence = (
        ProductTableWrite(
            key="selection",
            table="target_selection",
            primary_key=("run_id", "scan_id", "config_hash"),
            index_columns=("cell_uid",),
        ),
    )

    @classmethod
    def options_preset(cls) -> str:
        """The packaged rule as commented YAML, ready to paste under ``params:``."""
        return _PRESET.read_text("utf-8")

    @classmethod
    def configure(cls, options: TSEConfig, shared: SharedConfig) -> TargetSelectionRun:
        return TargetSelectionRun(rules=options, tracking_field=shared.tracking_field)

    def run(self, context: dict) -> dict:
        config: TargetSelectionRun = context["target_selection_config"]
        state: SelectionState = context["target_selection_state"] or SelectionState()
        engine = TargetSelectionEngine(config.rules, state.current)
        snapshot = snapshot_at(
            context["cells_so_far"],
            context["tracks_so_far"],
            context["scan_time"],
            tracking_field=config.tracking_field,
            growth_window_scans=config.rules.snapshot.growth_window_scans,
            trend_fields=config.rules.snapshot.trend_fields,
            track_summary=config.rules.snapshot.track_summary,
        )
        decision = engine.decide(snapshot)
        return {
            "selection": selection_frame(decision, config_hash(config.rules)),
            "target_selection_state": SelectionState(engine.current_target),
        }
