# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Module options that ship as a commented YAML preset.

A module whose options are a nested rule (target_selection, radar_command)
packages them as ``presets/<file>.yaml`` beside its science. The pipeline
file's ``params: {<module>: ...}`` entry is laid over the preset part by part.
"""

from collections.abc import Mapping
from importlib.resources import files
from typing import Any

import yaml

__all__ = ["laid_over", "preset_options", "preset_text"]


def preset_text(package: str, name: str) -> str:
    """The packaged preset ``presets/<name>.yaml`` of ``package``, comments and all."""
    return (files(package) / "presets" / f"{name}.yaml").read_text("utf-8")


def preset_options(package: str, name: str, key: str) -> dict[str, Any]:
    """The options under ``key`` of the packaged preset."""
    return yaml.safe_load(preset_text(package, name))[key]


def laid_over(base: Mapping[str, Any], given: Mapping[str, Any]) -> dict[str, Any]:
    """``base`` with ``given`` laid over it, part by part.

    Each option ``given`` names replaces the base's; where both hold a mapping
    for a part, only the keys ``given`` names change (``duration: {max_minutes:
    15}`` changes the cap and keeps every other part).
    """
    merged = dict(base)
    for part, options in given.items():
        if isinstance(options, Mapping) and isinstance(base.get(part), Mapping):
            merged[part] = {**base[part], **options}
        else:
            merged[part] = options
    return merged
