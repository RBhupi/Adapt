# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Module options that ship as a commented YAML preset.

A module whose options are a nested rule (target_selection, radar_command)
packages them as ``presets/<file>.yaml`` beside its science and returns the
text from ``options_preset``: the defaults the pipeline file's
``params: {<module>: ...}`` entry is laid over.
"""

from importlib.resources import files

__all__ = ["preset_text"]


def preset_text(package: str, name: str) -> str:
    """The packaged preset ``presets/<name>.yaml`` of ``package``, comments and all."""
    return (files(package) / "presets" / f"{name}.yaml").read_text("utf-8")
