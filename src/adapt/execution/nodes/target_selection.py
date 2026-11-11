# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""target_selection — after every tracked scan, choose the cell the radar should watch.

The rule (entry gates, score, hold gates, switching, duration cap) decides from
the run's cells up to this scan: the runtime hands them over as
``cells_so_far`` and ``tracks_so_far``, read back from the store once tracking
has written this scan. The decision is the ``selection`` output, one row per
scan and rule in the ``target_selection`` table; ``radar_command`` acts on it.
"""

from dataclasses import dataclass
from typing import Any

from adapt.contracts import ProductTableWrite, check_module_state, check_selection
from adapt.execution.module_registry import registry
from adapt.execution.nodes._options import laid_over, preset_options, preset_text
from adapt.modules.base import BaseModule
from adapt.modules.target_selection import (
    CurrentTarget,
    TargetSelectionEngine,
    TSEConfig,
    config_hash,
    snapshot_at,
)
from adapt.modules.target_selection.table import selection_frame

_PACKAGE, _PRESET = "adapt.modules.target_selection", "growing_isolated"


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
    # cell_identity: the scan's cells are tracked and stored before the rule reads them.
    inputs = [
        "target_selection_config",
        "cell_identity",
        "cells_so_far",
        "tracks_so_far",
        "scan_time",
        "target_selection_state",
    ]
    outputs = ["selection", "target_selection_state"]
    config_class = TSEConfig
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
    def default_params(cls) -> dict[str, Any]:
        return preset_options(_PACKAGE, _PRESET, cls.name)

    @classmethod
    def options_text(cls) -> str:
        """The packaged rule as commented YAML, ready to paste under ``params:``."""
        return preset_text(_PACKAGE, _PRESET)

    @classmethod
    def build_config(cls, cfg) -> TargetSelectionRun:
        given = cfg.module_params.get(cls.name, {})
        rules = TSEConfig.model_validate(laid_over(cls.default_params(), given))
        return TargetSelectionRun(rules=rules, tracking_field=cfg.global_.tracking_field)

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


registry.register(TargetSelectionModule)
