# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Radar command: turn a selected cell into a command for the radar.

Pure planning (footprints -> a box per minute -> the wire payload) plus the one
piece of network I/O in the modules: the transport that sends a command to the
radar API. The pipeline's ``radar_command`` module runs it every scan.
"""

from adapt.modules.radar_command.config import RadarCommandConfig, config_hash

__all__ = ["RadarCommandConfig", "config_hash"]
