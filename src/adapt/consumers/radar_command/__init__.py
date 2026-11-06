# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Radar Command — radar command files for the target selection engine's chosen cell.

A Ring 2 consumer of target selection: reads the selection file, reads the
store only through adapt.api.StoreClient, and writes command files plus an
audit ledger to its own output directory. It never controls the radar:
sending commands (the transport) is future work.
"""

from adapt.consumers.radar_command.config import RadarCommandConfig, load_config
from adapt.consumers.radar_command.runner import RadarCommandJob, process_selection, run

__all__ = [
    "RadarCommandConfig",
    "RadarCommandJob",
    "load_config",
    "process_selection",
    "run",
]
