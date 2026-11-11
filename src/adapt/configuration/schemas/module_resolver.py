# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Registry-driven module config engine.

Asks each module that is about to run to build its own frozen config from the
resolved InternalConfig via ``build_config``. Returns a dict keyed by the
context key each module declares (``<name>_config``).

This replaces the hardcoded ``materialize_module_configs`` — adding a module
never edits this file. The engine never knows which modules exist; it discovers
them from the registry.
"""

from collections.abc import Iterable
from typing import Any

from adapt.contracts import config_key
from adapt.execution.module_registry import registry


def resolve_module_configs(internal_config: Any, modules: Iterable[str]) -> dict[str, Any]:
    """Build one frozen config per module of ``modules``, the modules about to run.

    For each, calls ``build_config(internal_config)``, which may refuse what that
    module needs and was not given; a registered module that does not run is
    never configured. Modules that return None (need no config) contribute no key.
    """
    configs: dict[str, Any] = {}
    for name in modules:
        module_cfg = registry.get(name).build_config(internal_config)
        if module_cfg is not None:
            configs[config_key(name)] = module_cfg
    return configs
