# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Observability toggles live on the logging config section, defaulted by ParamConfig."""

from adapt.configuration.schemas.param import ParamConfig
from adapt.configuration.schemas.resolve import resolve_config
from adapt.configuration.schemas.user import UserConfig


def _run(**user) -> UserConfig:
    return UserConfig(radar="KTST", base_dir="/store", **user)


def test_logging_config_carries_observability_defaults() -> None:
    cfg = resolve_config(ParamConfig(), _run()).logging
    assert cfg.level == "INFO"
    assert cfg.enabled is True
    assert cfg.traces is True
    assert cfg.metrics is True
    assert cfg.json_logs is False
    assert cfg.console_logs is True
    assert cfg.console_level == "WARNING"


def test_a_logging_option_given_in_params_reaches_the_run() -> None:
    user = _run(logging={"console_level": "DEBUG", "json_logs": True})
    cfg = resolve_config(ParamConfig(), user).logging
    assert (cfg.console_level, cfg.json_logs) == ("DEBUG", True)
