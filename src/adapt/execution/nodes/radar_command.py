# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""radar_command — turn each scan's target selection into a command for the radar.

It boxes the selected cell's projected footprints (from the scan's analysis
dataset) with the cell's vertical extent, records the command in the
``radar_commands`` table and, when ``api`` is configured, sends it to the radar
API. It is the one module that talks to the outside world. A run over a stored
run never sends.
"""

import logging
from dataclasses import replace
from typing import Any

import pandas as pd

from adapt.contracts import (
    ProductTableWrite,
    check_module_state,
    check_radar_commands,
    check_selection,
)
from adapt.execution.module_registry import registry
from adapt.execution.nodes._options import laid_over, preset_options, preset_text
from adapt.modules.base import BaseModule
from adapt.modules.radar_command.command import CommandValidationError, to_json
from adapt.modules.radar_command.config import RadarCommandConfig, config_hash
from adapt.modules.radar_command.plan import build_plan
from adapt.modules.radar_command.record import (
    FAILED,
    CommandRecord,
    Outcome,
    command_frame,
    summary,
)
from adapt.modules.radar_command.target import SelectionRef, TargetUnavailable, target_geometry
from adapt.modules.radar_command.transport import SendError, api_key, send

logger = logging.getLogger(__name__)

_PACKAGE, _PRESET = "adapt.modules.radar_command", "sandbox"
# The outcomes after which a plan counts as published (a refused send is not
# repeated: the next scan plans afresh).
_NEWLY_PUBLISHED = frozenset({Outcome.PUBLISHED, Outcome.SENT, Outcome.SEND_FAILED})
_VOLUME_STATS = "cell_volume_stats_rows"
# Carried from scan to scan: the ids of the plans this run has published.
_STATE = "radar_command_state"


class RadarCommandModule(BaseModule):
    """Plan, record and (when configured) send the command for each scan's target."""

    name = "radar_command"
    summary = "command the radar to the selected cell; needs target_selection"
    inputs = [
        "radar_command_config",
        "selection",
        "analysis_ds",
        _VOLUME_STATS,
        "run_id",
        "scan_id",
        "scan_time",
        _STATE,
    ]
    outputs = ["radar_commands", _STATE]
    config_class = RadarCommandConfig
    input_contracts = {"selection": check_selection}
    output_contracts = {"radar_commands": check_radar_commands, _STATE: check_module_state}
    persistence = (
        ProductTableWrite(
            key="radar_commands",
            table="radar_commands",
            primary_key=("run_id", "scan_id", "config_hash"),
            index_columns=("plan_id", "cell_uid"),
        ),
    )

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return preset_options(_PACKAGE, _PRESET, cls.name)

    @classmethod
    def options_text(cls) -> str:
        """The packaged options as commented YAML, ready to paste under ``params:``."""
        return preset_text(_PACKAGE, _PRESET)

    @classmethod
    def build_config(cls, cfg) -> RadarCommandConfig:
        """The run's config. A run over a stored run never sends; a live run that
        sends must have its API key in the environment now."""
        given = cfg.module_params.get(cls.name, {})
        config = RadarCommandConfig.model_validate(laid_over(cls.default_params(), given))
        if config.api is None:
            return config
        if cfg.source == "stored_run":
            return config.model_copy(update={"api": None})
        api_key(config.api)  # raises naming the variable when it is unset
        return config

    @classmethod
    def inputs_for(cls, config: RadarCommandConfig) -> list[str]:
        """A fixed vertical band needs no measured cell base and top."""
        if config.extent.vertical_band is None:
            return cls.inputs
        return [key for key in cls.inputs if key != _VOLUME_STATS]

    def run(self, context: dict) -> dict:
        config: RadarCommandConfig = context["radar_command_config"]
        published: frozenset[str] = context[_STATE] or frozenset()
        record = self._decide(config, context, published)
        if record.outcome in _NEWLY_PUBLISHED and record.plan_id is not None:
            published = published | {record.plan_id}
        level = logging.WARNING if record.outcome in FAILED else logging.INFO
        # On the console too: what the radar was asked to do is what an operator watches.
        logger.log(
            level, summary(record, context["run_id"], context["scan_id"]), extra={"console": True}
        )
        return {"radar_commands": command_frame(record), _STATE: published}

    def _decide(
        self, config: RadarCommandConfig, context: dict, published: frozenset[str]
    ) -> CommandRecord:
        selection = context["selection"].iloc[0]
        record = CommandRecord(
            outcome=Outcome.NO_TARGET,
            config_hash=config_hash(config),
            selection_config_hash=selection["config_hash"],
        )
        if pd.isna(selection["cell_uid"]):
            return record
        ref = SelectionRef(
            run_id=context["run_id"],
            scan_id=context["scan_id"],
            scan_time=context["scan_time"],
            cell_uid=selection["cell_uid"],
            config_hash=selection["config_hash"],
        )
        record = replace(record, cell_uid=ref.cell_uid)
        try:
            geometry = target_geometry(
                context["analysis_ds"], ref, context.get(_VOLUME_STATS), config.extent.vertical_band
            )
            plan = build_plan(geometry, ref, config)
        except TargetUnavailable as exc:
            return replace(
                record, outcome=Outcome.TARGET_UNAVAILABLE, detail=f"{type(exc).__name__}: {exc}"
            )
        record = replace(
            record,
            plan_id=plan.plan_id,
            n_entries=len(plan.command.entries),
            truncated=plan.truncated,
            vertical_source=plan.vertical_source,
        )
        try:
            command_json = to_json(plan.command)
        except CommandValidationError as exc:
            return replace(record, outcome=Outcome.VALIDATION_FAILED, detail=str(exc))
        record = replace(record, command_json=command_json)
        if plan.plan_id in published:
            return replace(record, outcome=Outcome.DUPLICATE)
        if config.api is None:
            return replace(record, outcome=Outcome.PUBLISHED)
        try:
            response = send(command_json, config.api, api_key(config.api))
        except SendError as exc:
            return replace(record, outcome=Outcome.SEND_FAILED, detail=str(exc))
        return replace(record, outcome=Outcome.SENT, detail=response)


registry.register(RadarCommandModule)
