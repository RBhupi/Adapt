# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The run's ingress source.

A source acquires raw scans into the store (via the injected acquisition
gateway) and feeds the resulting queue messages to the processor. Which
sources exist is the catalog's (``pipeline.sources`` of
``configuration/defaults.yaml``, registered by
``adapt.execution.source_registry``); this file names none. The orchestrator
builds the source ``config.source`` names, uniformly with
(config, result_queue, acquire).
"""

from adapt.configuration.schemas.errors import ConfigError
from adapt.contracts.source import ScanSource
from adapt.execution.source_registry import catalog_source_names, source_registry

__all__ = ["check_source", "source_class"]


def source_class(name: str) -> type[ScanSource]:
    """The class of the source ``name``, the catalog loaded first.

    Raises
    ------
    KeyError
        No source of that name is registered.
    """
    catalog_source_names()
    return source_registry.get(name)


def check_source(config) -> None:
    """Refuse a run its source cannot run (e.g. the folder a local run reads),
    before the run is recorded.

    Raises
    ------
    ConfigError
        The source refuses ``config``; the message is the source's own.
    """
    try:
        source_class(config.source).check(config)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
