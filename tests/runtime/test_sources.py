# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests for the ingress source plugin system.

Sources are registered, swappable-by-name plugins that push file paths into the
processor queue. AwsNexradDownloader (download) and LocalDirectorySource
(already-present files) both satisfy the ScanSource interface.
"""

import re

import pytest

from tests.helpers.run_configs import new_run_config

pytestmark = pytest.mark.unit


class TestSourceRegistry:
    def test_registry_resolves_builtin_sources(self):
        from adapt.runtime.sources import source_registry
        from adapt.sources.aws_nexrad.source import AwsNexradDownloader
        from adapt.sources.local_directory.source import LocalDirectorySource

        assert source_registry.get("aws_nexrad") is AwsNexradDownloader
        assert source_registry.get("local_directory") is LocalDirectorySource

    def test_unknown_source_raises(self):
        from adapt.runtime.sources import source_registry

        with pytest.raises(KeyError, match="unknown_source"):
            source_registry.get("unknown_source")

    def test_duplicate_registration_raises(self):
        from adapt.runtime.sources import SourceRegistry

        reg = SourceRegistry()
        reg.register("dup", object)
        with pytest.raises(RuntimeError, match="dup"):
            reg.register("dup", object)

    def test_names_lists_builtin_sources(self):
        from adapt.runtime.sources import source_registry

        assert {"aws_nexrad", "local_directory"} <= set(source_registry.names())

    def test_every_registered_source_satisfies_scan_source_protocol(self):
        """The orchestrator drives sources only through the ScanSource interface."""
        from adapt.contracts.source import ScanSource
        from adapt.runtime.sources import source_registry

        for name in source_registry.names():
            cls = source_registry.get(name)
            assert issubclass(cls, ScanSource), (
                f"Source '{name}' ({cls.__name__}) does not satisfy the ScanSource "
                "protocol — the orchestrator cannot drive it. See contracts/source.py."
            )


class TestCheckSource:
    def test_a_folder_that_does_not_exist_is_refused_keeping_the_source_text(
        self, store_root, tmp_path
    ):
        """The cli checks the run's source before the run starts; the source's own
        refusal reaches the user as a configuration error, word for word."""
        from adapt.configuration.schemas.errors import ConfigError
        from adapt.runtime.sources import check_source

        absent = tmp_path / "absent"
        run = {
            "source": "local_directory",
            "radar": "KLOT",
            "mode": "historical",
            "start_time": "2024-01-01T12:00:00Z",
            "end_time": "2024-01-01T13:00:00Z",
            "source_dir": str(absent),
        }

        refusal = re.escape(f"run.source_dir {absent} is not a folder")
        with pytest.raises(ConfigError, match=refusal):
            check_source(new_run_config(store_root, run=run))
