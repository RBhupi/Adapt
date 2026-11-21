# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The run's source, checked before the run is recorded.

Which sources exist and how they are registered is tested in
``tests/execution/test_source_registration.py``.
"""

import re

import pytest

from tests.helpers.run_configs import new_run_config

pytestmark = pytest.mark.unit


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
