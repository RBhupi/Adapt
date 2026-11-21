# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Ingress source registry.

A source is a swappable, registered-by-name plugin that acquires raw scans into
the store (via the injected acquisition gateway) and feeds the resulting queue
messages to the processor. The orchestrator resolves the source named by
``config.source`` and constructs it uniformly with
(config, result_queue, acquire).

Built-in sources, one package each under ``adapt.sources``:
- ``aws_nexrad``      — AwsNexradDownloader (download from S3, realtime/historical)
- ``local_directory`` — LocalDirectorySource (acquire already-present files in order)
- ``stored_run``      — StoredRunSource (the scans a run of the store already has)

Adding a source: implement the ScanSource interface (see contracts/source.py) and
call ``source_registry.register(name, cls)``.
"""

from adapt.configuration.schemas.errors import ConfigError
from adapt.contracts.source import ScanSource
from adapt.sources.aws_nexrad.source import AwsNexradDownloader
from adapt.sources.local_directory.source import LocalDirectorySource
from adapt.sources.stored_run.source import StoredRunSource

__all__ = ["SourceRegistry", "check_source", "source_registry"]


class SourceRegistry:
    """Name → source class. Resolved by the orchestrator from ``config.source``."""

    def __init__(self) -> None:
        self._sources: dict[str, type[ScanSource]] = {}

    def register(self, name: str, source_class: type[ScanSource]) -> None:
        if name in self._sources:
            raise RuntimeError(f"Source '{name}' is already registered.")
        self._sources[name] = source_class

    def get(self, name: str) -> type[ScanSource]:
        if name not in self._sources:
            raise KeyError(f"Source '{name}' is not registered.")
        return self._sources[name]

    def names(self) -> list[str]:
        return list(self._sources)


def check_source(config) -> None:
    """Refuse a run its source cannot run (e.g. the folder a local run reads),
    before the run is recorded.

    Raises
    ------
    ConfigError
        The source refuses ``config``; the message is the source's own.
    """
    try:
        source_registry.get(config.source).check(config)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


source_registry = SourceRegistry()
source_registry.register("aws_nexrad", AwsNexradDownloader)
source_registry.register("local_directory", LocalDirectorySource)
source_registry.register("stored_run", StoredRunSource)
