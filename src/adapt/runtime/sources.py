# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The run's ingress source, checked and built from the run config.

A source acquires raw scans into the store (via the injected acquisition
gateway) and feeds the resulting queue messages to the processor. Which
sources exist is the catalog's (``pipeline.sources`` of
``configuration/defaults.yaml``, registered by
``adapt.execution.source_registry``); this file names none. Every source is
handed the same four things: the run's values (``SourceRun``), its own
options, the queue and the gateway.
"""

from pydantic import BaseModel

from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.internal import InternalConfig
from adapt.contracts.source import ScanQueue, ScanSource, SourceRun
from adapt.execution.source_registry import catalog_source_names, source_registry
from adapt.runtime.acquire import StoreAcquirer

__all__ = ["build_source", "check_source", "source_run"]


def source_run(config: InternalConfig) -> SourceRun:
    """The values of the run ``config`` describes that its source is handed."""
    return SourceRun(
        radar=config.downloader.radar,
        mode=config.mode,
        start_time=config.downloader.start_time,
        end_time=config.downloader.end_time,
        source_dir=config.source_dir,
    )


def _source_options(config: InternalConfig) -> BaseModel | None:
    """The options of the run's source, each read by its name from the run
    config's downloader settings; ``None`` for a source without options."""
    options_class = source_registry.options_class(config.source)
    if options_class is None:
        return None
    return options_class(
        **{name: getattr(config.downloader, name) for name in options_class.model_fields}
    )


def check_source(config: InternalConfig) -> None:
    """Refuse a run its source cannot run (e.g. the folder a local run reads),
    before the run is recorded.

    Raises
    ------
    ConfigError
        The source refuses the run; the message is the source's own.
    KeyError
        No source of the run's name is registered.
    """
    catalog_source_names()
    try:
        source_registry.get(config.source).check(source_run(config), _source_options(config))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def build_source(config: InternalConfig, queue: ScanQueue, acquire: StoreAcquirer) -> ScanSource:
    """The run's source, handed the run's values, its own options, ``queue`` and
    ``acquire``.

    Raises
    ------
    KeyError
        No source of the run's name is registered.
    """
    catalog_source_names()
    source_class = source_registry.get(config.source)
    return source_class(source_run(config), _source_options(config), queue, acquire)
