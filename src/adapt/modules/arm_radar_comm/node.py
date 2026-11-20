# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""arm_radar_comm — turn each scan's target selection into a command for the radar.

It boxes the selected cell's projected footprints (from the scan's analysis
dataset) with the cell's vertical extent, records the command in the
``radar_commands`` table and, when ``api`` is configured, publishes it to the
radar API's command queue. It is the one module that talks to the outside world.
A run over a stored run never sends, and a scan older than ``api.max_scan_age_seconds`` as this
module starts (``wall_time``, the machine's time the runtime provides) is never
sent either: its command is recorded and held back.
"""

import logging
import math
from dataclasses import replace
from datetime import timedelta
from importlib.resources import files

import pandas as pd

from adapt.contracts import (
    ProductTableWrite,
    SharedConfig,
    check_module_state,
    check_radar_commands,
    check_selection,
)
from adapt.modules.arm_radar_comm.command import CommandValidationError, to_json
from adapt.modules.arm_radar_comm.config import (
    ApiConfig,
    RadarCommandConfig,
    RadarCommandRun,
    config_hash,
)
from adapt.modules.arm_radar_comm.plan import build_plan
from adapt.modules.arm_radar_comm.record import (
    FAILED,
    CommandRecord,
    Outcome,
    command_frame,
    summary,
)
from adapt.modules.arm_radar_comm.target import SelectionRef, TargetUnavailable, target_geometry
from adapt.modules.arm_radar_comm.transport import (
    EndpointError,
    NotSent,
    SendError,
    endpoint,
    send,
    user_id,
)
from adapt.modules.base import BaseModule

logger = logging.getLogger(__name__)

_PRESET = files("adapt.modules.arm_radar_comm") / "presets" / "sandbox.yaml"
# The outcomes after which a plan counts as published (a refused send is not
# repeated: the next scan plans afresh).
_NEWLY_PUBLISHED = frozenset(
    {Outcome.PUBLISHED, Outcome.SENT, Outcome.NOT_ACKNOWLEDGED, Outcome.SEND_FAILED}
)
_VOLUME_STATS = "cell_volume_stats_rows"
# Carried from scan to scan: the ids of the plans this run has published.
_STATE = "arm_radar_comm_state"


class ArmRadarCommModule(BaseModule):
    """Plan, record and (when configured) send the command for each scan's target."""

    name = "arm_radar_comm"
    summary = "command the radar to the selected cell; needs target_selection"
    inputs = [
        "arm_radar_comm_config",
        "selection",
        "analysis_ds",
        _VOLUME_STATS,
        "run_id",
        "scan_id",
        "scan_time",
        "wall_time",
        _STATE,
    ]
    outputs = ["radar_commands", _STATE]
    options_class = RadarCommandConfig
    every_option_written = True  # what is sent to a radar is never a default
    rows_keyed_by_options = True  # config_hash is in the table's key
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
    def options_preset(cls) -> str:
        """Every option as commented YAML, a dry run, to copy under ``params:``."""
        return _PRESET.read_text("utf-8")

    @classmethod
    def configure(cls, options: RadarCommandConfig, shared: SharedConfig) -> RadarCommandRun:
        """The run's config: the options and the user id the environment holds now.
        A run over a stored run never sends; a run that sends must have its API
        address, radar, key and CA file in the environment now. A variable that is
        missing or unusable refuses the run, by name."""
        run = RadarCommandRun(options=options, user_id=user_id(options.radar))
        if options.api is None:
            return run
        if shared.source == "stored_run":
            return replace(run, options=options.model_copy(update={"api": None}))
        endpoint(options.api)
        return run

    @classmethod
    def inputs_for(cls, config: RadarCommandRun) -> list[str]:
        """A fixed vertical band needs no measured cell base and top."""
        if config.options.extent.vertical_band is None:
            return cls.inputs
        return [key for key in cls.inputs if key != _VOLUME_STATS]

    def run(self, context: dict) -> dict:
        run: RadarCommandRun = context["arm_radar_comm_config"]
        published: frozenset[str] = context[_STATE] or frozenset()
        record = self._decide(run, context, published)
        if record.outcome in _NEWLY_PUBLISHED and record.plan_id is not None:
            published = published | {record.plan_id}
        level = logging.WARNING if record.outcome in FAILED else logging.INFO
        # On the console too: what the radar was asked to do is what an operator watches.
        logger.log(level, summary(record), extra={"console": True})
        return {"radar_commands": command_frame(record), _STATE: published}

    def _decide(
        self, run: RadarCommandRun, context: dict, published: frozenset[str]
    ) -> CommandRecord:
        config = run.options
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
            plan = build_plan(geometry, ref, config, run.user_id)
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
        held_back = _too_old(context["wall_time"] - context["scan_time"], config.api)
        if held_back is not None:
            return replace(record, outcome=Outcome.STALE, detail=held_back)
        try:
            acknowledged = send(command_json, endpoint(config.api))
        except (NotSent, EndpointError) as exc:  # settings can be lost while a run goes on
            return replace(record, outcome=Outcome.SEND_FAILED, detail=str(exc))
        except SendError as exc:  # it went out; the answer was an error, something else, or lost
            return replace(record, outcome=Outcome.NOT_ACKNOWLEDGED, detail=str(exc))
        return replace(record, outcome=Outcome.SENT, detail=acknowledged)


def _too_old(age: timedelta, api: ApiConfig) -> str | None:
    """Why a scan of this age is not sent; None when it is fresh enough."""
    seconds = age.total_seconds()
    if seconds < 0:  # a clock is wrong (this machine's or the radar's): freshness is unknown
        return f"scan time is {math.ceil(-seconds)} s ahead of this machine's clock"
    if seconds > api.max_scan_age_seconds:
        return (
            f"scan is {math.ceil(seconds)} s old; the limit is {api.max_scan_age_seconds:g} s "
            "(arm_radar_comm.api.max_scan_age_seconds)"
        )
    return None
