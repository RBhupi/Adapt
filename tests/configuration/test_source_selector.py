# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests for the source selector config: which ingress source feeds the pipeline.

`source` names a registered source plugin (default "aws_nexrad"); `source_dir`
is the directory a local source scans.
"""

import pytest

pytestmark = pytest.mark.unit

from adapt.configuration.schemas.param import ParamConfig  # noqa: E402
from adapt.configuration.schemas.resolve import resolve_config  # noqa: E402
from adapt.configuration.schemas.user import UserConfig  # noqa: E402


class TestSourceSelector:
    def test_source_defaults_to_aws_nexrad(self, tmp_path):
        cfg = resolve_config(ParamConfig(), UserConfig(base_dir=str(tmp_path), radar="KLOT"))
        assert cfg.source == "aws_nexrad"
        assert cfg.source_dir is None

    def test_user_can_select_local_directory_source(self, tmp_path):
        user = UserConfig(
            base_dir=str(tmp_path), radar="KLOT", source="local_directory", source_dir="/data/in"
        )
        cfg = resolve_config(ParamConfig(), user)
        assert cfg.source == "local_directory"
        assert cfg.source_dir == "/data/in"
