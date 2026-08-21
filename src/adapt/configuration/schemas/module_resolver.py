# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Registry-driven module config engine.

Iterates registered modules and asks each to build its own frozen config from
the resolved InternalConfig via ``build_config``. Returns a dict keyed by the
context key each module declares (``<name>_config``).

This replaces the hardcoded ``materialize_module_configs`` — adding a module
never edits this file. The engine never knows which modules exist; it discovers
them from the registry.
"""

from typing import Any

from adapt.execution.module_registry import registry


def resolve_module_configs(internal_config: Any) -> dict[str, Any]:
    """Build one frozen config per registered module.

    For each registered module, calls ``build_config(internal_config)``.
    Modules that return None (need no config) contribute no key.
    """
    configs: dict[str, Any] = {}
    for name in registry.list_modules():
        module_cfg = registry.get(name).build_config(internal_config)
        if module_cfg is not None:
            configs[f"{name}_config"] = module_cfg
    return configs
