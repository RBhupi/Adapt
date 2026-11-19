# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Observability toggles live on the run config's logging section."""


def test_logging_config_carries_observability_defaults(internal_config) -> None:
    cfg = internal_config.logging
    assert cfg.level == "INFO"
    assert cfg.enabled is True
    assert cfg.traces is True
    assert cfg.metrics is True
    assert cfg.json_logs is False
    assert cfg.console_logs is True
    assert cfg.console_level == "WARNING"


def test_a_logging_option_given_in_params_reaches_the_run(make_config) -> None:
    cfg = make_config(params={"logging": {"console_level": "DEBUG", "json_logs": True}}).logging
    assert (cfg.console_level, cfg.json_logs) == ("DEBUG", True)
