# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Configuration schemas and resolution for the Adapt pipeline.

ParamConfig (expert defaults) + UserConfig (overrides) resolve to the frozen
InternalConfig runtime code sees (``resolve.resolve_config``); module configs are
built from it by ``resolve_module_configs``. Pipeline files are loaded and
translated into this layer by ``adapt.configuration.pipeline_file``.

Exports
-------
ConfigError : exception
    A configuration value is invalid; carries a ready-to-print message.
resolve_module_configs : function
    One frozen config per registered module.
"""

from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.module_resolver import resolve_module_configs

__all__ = ["ConfigError", "resolve_module_configs"]
