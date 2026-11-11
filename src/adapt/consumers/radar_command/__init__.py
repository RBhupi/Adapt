# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Radar Command — radar command files for the target selection engine's chosen cell.

A Ring 2 consumer of target selection: reads the selection file, reads the
store only through adapt.api.StoreClient, and writes command files plus an
audit ledger under the output directory the launcher gives it
(``<root>/consumers/<run_id>/radar_command/``) — never into the store's
databases. With ``api`` configured it also sends each published command to
the radar API.
"""

from adapt.consumers.radar_command.config import RadarCommandConfig
from adapt.consumers.radar_command.runner import RadarCommandJob, process_selection, run

__all__ = [
    "RadarCommandConfig",
    "RadarCommandJob",
    "process_selection",
    "run",
]
