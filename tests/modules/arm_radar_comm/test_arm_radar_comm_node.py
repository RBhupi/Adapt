# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""ArmRadarCommModule: one scan's selection -> the command recorded (and sent)."""

import json
import logging
from dataclasses import replace
from datetime import timedelta

import pandas as pd
import pytest

from adapt.configuration.schemas.module_resolver import module_options
from adapt.contracts import check_radar_commands
from adapt.modules.arm_radar_comm import RadarCommandConfig, RadarCommandRun, node
from adapt.modules.arm_radar_comm.node import ArmRadarCommModule
from adapt.modules.arm_radar_comm.transport import NotSent, SendError
from tests.helpers import radar_store
from tests.helpers.pipeline_files import (
    API,
    API_KEY,
    API_URL,
    RADAR_COMMAND,
    USER_ID,
    export_api,
)
from tests.helpers.shared_config import SHARED

pytestmark = pytest.mark.unit

_BAND = {"altitude_min_m": 500.0, "altitude_max_m": 12000.0}


def _config(**parts) -> RadarCommandConfig:
    return RadarCommandConfig.model_validate({**RADAR_COMMAND, **parts})


def _selection(cell_uid: str | None) -> pd.DataFrame:
    return pd.DataFrame([{"config_hash": "0123456789abcdef", "cell_uid": cell_uid}])


def _context(config, cell_uid=radar_store.UID_A, scan=1, **overrides) -> dict:
    """What the module runs with: ``config`` as configured, or options with the exported user."""
    if isinstance(config, RadarCommandConfig):
        config = RadarCommandRun(options=config, user_id=USER_ID)
    outputs = radar_store.scan_outputs(scan)
    return {
        "arm_radar_comm_config": config,
        "selection": _selection(cell_uid),
        "analysis_ds": outputs["analysis_ds"],
        "cell_volume_stats_rows": outputs["cell_volume_stats_rows"],
        "run_id": "run-1",
        "scan_id": f"scan-{scan}",
        "scan_time": radar_store.SCAN_TIMES[scan],
        "wall_time": radar_store.SCAN_TIMES[scan] + timedelta(seconds=120),  # a fresh scan
        "arm_radar_comm_state": None,  # nothing published yet
        **overrides,
    }


def _at(age_seconds: float):
    """The machine's time when scan 1 (the one ``_context`` hands on) is that old."""
    return radar_store.SCAN_TIMES[1] + timedelta(seconds=age_seconds)


def _row(result: dict) -> pd.Series:
    check_radar_commands(result["radar_commands"])
    return result["radar_commands"].iloc[0]


def test_a_selected_cell_gets_a_published_command():
    row = _row(ArmRadarCommModule().run(_context(_config())))

    assert row["outcome"] == "PUBLISHED"
    assert row["cell_uid"] == radar_store.UID_A
    assert row["selection_config_hash"] == "0123456789abcdef"
    assert row["vertical_source"] == "cell_volume_stats"
    assert row["n_entries"] == 12
    (payload,) = json.loads(row["command_json"]).values()
    assert payload["target_id"] == row["plan_id"]
    assert (payload["scan_pattern"], payload["user_id"]) == ("ppi_sector", "operator")


def test_no_selected_cell_is_recorded_as_no_target():
    row = _row(ArmRadarCommModule().run(_context(_config(), cell_uid=None)))

    assert row["outcome"] == "NO_TARGET"
    assert row["plan_id"] is None and row["command_json"] is None


def test_a_cell_that_cannot_be_located_is_recorded_with_why():
    row = _row(ArmRadarCommModule().run(_context(_config(), cell_uid="uid-zz")))

    assert row["outcome"] == "TARGET_UNAVAILABLE"
    assert "uid-zz" in row["detail"]


def test_the_same_plan_is_not_published_twice_by_one_run():
    """What was published is carried from scan to scan as the module's state."""
    first_result = ArmRadarCommModule().run(_context(_config()))
    first = _row(first_result)
    published = first_result["arm_radar_comm_state"]

    again = _row(ArmRadarCommModule().run(_context(_config(), arm_radar_comm_state=published)))

    assert published == frozenset({first["plan_id"]})
    assert again["outcome"] == "DUPLICATE"
    assert again["plan_id"] == first["plan_id"]


def test_a_scan_without_a_command_leaves_the_state_as_it_was():
    result = ArmRadarCommModule().run(
        _context(_config(), cell_uid=None, arm_radar_comm_state=frozenset({"earlier-plan"}))
    )

    assert result["arm_radar_comm_state"] == frozenset({"earlier-plan"})


def test_a_fixed_band_needs_no_measured_extent():
    config = _config(extent={"horizontal_buffer_km": 1.0, "vertical_band": _BAND})
    context = _context(config)
    del context["cell_volume_stats_rows"]

    row = _row(ArmRadarCommModule().run(context))

    assert "cell_volume_stats_rows" not in ArmRadarCommModule.inputs_for(
        context["arm_radar_comm_config"]
    )
    assert "cell_volume_stats_rows" in ArmRadarCommModule.inputs_for(
        _context(_config())["arm_radar_comm_config"]
    )
    assert (row["outcome"], row["vertical_source"]) == ("PUBLISHED", "config")


def test_a_configured_api_is_sent_the_command_with_the_key(monkeypatch):
    sent = []
    export_api(monkeypatch)
    monkeypatch.setattr(
        node, "send", lambda command_json, target: sent.append((command_json, target)) or "queued"
    )

    row = _row(ArmRadarCommModule().run(_context(_config(api=API))))

    assert (row["outcome"], row["detail"]) == ("SENT", "queued")
    ((command_json, target),) = sent
    assert command_json == row["command_json"]
    assert target.url == f"{API_URL}/publish/command"
    assert target.key.get_secret_value() == API_KEY
    assert API_KEY not in row.to_json()


def test_the_command_sent_is_one_wrapped_auto_target(monkeypatch):
    sent = []
    export_api(monkeypatch)
    monkeypatch.setattr(node, "send", lambda command_json, target: sent.append(command_json) or "")

    ArmRadarCommModule().run(_context(_config(api=API)))

    assert list(json.loads(sent[0])) == ["AutoTarget"]


def test_an_api_that_refuses_is_recorded_and_does_not_raise(monkeypatch):
    def refuse(command_json, target):
        raise SendError("https://radar.example.test/publish/command answered HTTP 503: down")

    export_api(monkeypatch)
    monkeypatch.setattr(node, "send", refuse)

    row = _row(ArmRadarCommModule().run(_context(_config(api=API))))

    assert row["outcome"] == "NOT_ACKNOWLEDGED"
    assert "HTTP 503" in row["detail"]
    assert row["command_json"] is not None  # what would have been sent is kept


# --- only a fresh scan is sent ---------------------------------------------------


def _sends(monkeypatch) -> list:
    sent: list = []
    export_api(monkeypatch)
    monkeypatch.setattr(node, "send", lambda command_json, target: sent.append(target) or "ok")
    return sent


@pytest.mark.parametrize("age", [0.0, 299.0, 300.0])
def test_a_scan_within_the_age_limit_is_sent(monkeypatch, age):
    sent = _sends(monkeypatch)

    row = _row(ArmRadarCommModule().run(_context(_config(api=API), wall_time=_at(age))))

    assert row["outcome"] == "SENT"
    assert len(sent) == 1


@pytest.mark.parametrize("age", [301.0, 811.0, 86400.0])
def test_a_scan_older_than_the_limit_is_recorded_and_not_sent(monkeypatch, age):
    sent = _sends(monkeypatch)

    row = _row(ArmRadarCommModule().run(_context(_config(api=API), wall_time=_at(age))))

    assert sent == []
    assert row["outcome"] == "STALE"
    assert f"{age:.0f} s old" in row["detail"] and "300 s" in row["detail"]
    assert "max_scan_age_seconds" in row["detail"]
    assert row["command_json"] is not None  # what would have been sent is kept


def test_an_age_just_over_the_limit_is_not_reported_as_the_limit(monkeypatch):
    _sends(monkeypatch)

    row = _row(ArmRadarCommModule().run(_context(_config(api=API), wall_time=_at(300.4))))

    assert row["outcome"] == "STALE"
    assert "301 s old; the limit is 300 s" in row["detail"]


def test_a_scan_ahead_of_the_machines_clock_is_held_back(monkeypatch):
    # The limit cannot be trusted when the clock (the machine's or the radar's) is off.
    sent = _sends(monkeypatch)

    row = _row(ArmRadarCommModule().run(_context(_config(api=API), wall_time=_at(-400.0))))

    assert (row["outcome"], sent) == ("STALE", [])
    assert "400 s ahead of this machine's clock" in row["detail"]


def test_an_old_scan_whose_command_already_went_out_stays_a_duplicate(monkeypatch):
    _sends(monkeypatch)
    first = ArmRadarCommModule().run(_context(_config(api=API)))

    again = _row(
        ArmRadarCommModule().run(
            _context(
                _config(api=API),
                wall_time=_at(811.0),
                arm_radar_comm_state=first["arm_radar_comm_state"],
            )
        )
    )

    assert again["outcome"] == "DUPLICATE"


def test_the_age_limit_is_the_one_the_options_give(monkeypatch):
    sent = _sends(monkeypatch)
    patient = _config(api={**API, "max_scan_age_seconds": 900})

    row = _row(ArmRadarCommModule().run(_context(patient, wall_time=_at(811.0))))

    assert (row["outcome"], len(sent)) == ("SENT", 1)


def test_a_scan_too_old_to_send_is_not_marked_as_published(monkeypatch):
    _sends(monkeypatch)

    result = ArmRadarCommModule().run(_context(_config(api=API), wall_time=_at(811.0)))

    assert not result["arm_radar_comm_state"]


def test_a_dry_run_records_the_command_of_an_old_scan_all_the_same():
    row = _row(ArmRadarCommModule().run(_context(_config(), wall_time=_at(86400.0))))

    assert row["outcome"] == "PUBLISHED"


def test_a_scan_too_old_to_send_says_so_on_the_console_without_marks(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _sends(monkeypatch)

    row = _row(ArmRadarCommModule().run(_context(_config(api=API), wall_time=_at(811.0))))

    assert _console_line(caplog) == (
        f"arm_radar_comm: command {row['plan_id']} held back for cell {radar_store.UID_A}"
        ": scan is 811 s old; the limit is 300 s (arm_radar_comm.api.max_scan_age_seconds)"
    )
    (record,) = [r for r in caplog.records if "arm_radar_comm:" in r.getMessage()]
    assert record.levelno == logging.INFO  # held back on purpose: not a failure


def _console_line(caplog) -> str:
    (record,) = [r for r in caplog.records if "arm_radar_comm:" in r.getMessage()]
    assert record.console is True
    return record.getMessage()


def test_a_dry_run_says_on_the_console_that_nothing_was_sent(caplog):
    caplog.set_level(logging.INFO)

    row = _row(ArmRadarCommModule().run(_context(_config())))

    line = _console_line(caplog)
    assert line == (
        f"arm_radar_comm: command {row['plan_id']} written for cell {radar_store.UID_A} "
        "(dry run, nothing sent)"
    )


def test_a_command_sent_and_acknowledged_gets_two_ticks_on_the_console(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    export_api(monkeypatch)
    monkeypatch.setattr(node, "send", lambda command_json, target: "HTTP 200: published, and more")

    row = _row(ArmRadarCommModule().run(_context(_config(api=API))))

    assert _console_line(caplog) == (
        f"✓✓ arm_radar_comm: command {row['plan_id']} sent for cell {radar_store.UID_A} "
        "(queued by the radar API)"
    )


def test_a_command_sent_but_not_acknowledged_gets_a_tick_then_a_cross(monkeypatch, caplog):
    def refuse(command_json, target):
        raise SendError("HTTP 401: " + "no such key " * 40)

    caplog.set_level(logging.INFO)
    export_api(monkeypatch)
    monkeypatch.setattr(node, "send", refuse)

    row = _row(ArmRadarCommModule().run(_context(_config(api=API))))

    line = _console_line(caplog)
    assert row["outcome"] == "NOT_ACKNOWLEDGED"
    assert line.startswith(f"✓✗ arm_radar_comm: command {row['plan_id']} not acknowledged for cell")
    assert "HTTP 401: no such key" in line
    assert len(line) < 200 < len(row["detail"])  # the whole answer is in the table


def test_a_command_that_did_not_go_out_gets_a_cross_and_no_second_mark(monkeypatch, caplog):
    def unreachable(command_json, target):
        raise NotSent("unreachable: connection refused")

    caplog.set_level(logging.INFO)
    export_api(monkeypatch)
    monkeypatch.setattr(node, "send", unreachable)

    row = _row(ArmRadarCommModule().run(_context(_config(api=API))))

    assert row["outcome"] == "SEND_FAILED"
    assert _console_line(caplog) == (
        f"✗– arm_radar_comm: command {row['plan_id']} not sent for cell {radar_store.UID_A}"
        ": unreachable: connection refused"
    )


@pytest.mark.parametrize("failure", [NotSent("unreachable"), SendError("HTTP 500: down")])
def test_a_command_that_failed_is_not_sent_again_at_the_next_scan(monkeypatch, failure):
    def fail(command_json, target):
        raise failure

    export_api(monkeypatch)
    monkeypatch.setattr(node, "send", fail)
    first = ArmRadarCommModule().run(_context(_config(api=API)))

    again = _row(
        ArmRadarCommModule().run(
            _context(_config(api=API), arm_radar_comm_state=first["arm_radar_comm_state"])
        )
    )

    assert again["outcome"] == "DUPLICATE"


def test_api_settings_lost_during_the_run_are_recorded_and_do_not_raise(monkeypatch):
    export_api(monkeypatch)
    config = _configured("aws_nexrad", {**RADAR_COMMAND, "api": API})
    monkeypatch.delenv("ADAPT_TEST_CA")

    row = _row(ArmRadarCommModule().run(_context(config)))

    assert row["outcome"] == "SEND_FAILED"
    assert "ADAPT_TEST_CA" in row["detail"]
    assert row["command_json"] is not None


def _configured(source: str, params: dict) -> RadarCommandRun:
    """The config a run from ``source`` gets from ``params.arm_radar_comm``: these parts
    in place of those of ``RADAR_COMMAND``, which gives every option."""
    options = module_options("arm_radar_comm", {**RADAR_COMMAND, **params}, source="a test")
    return ArmRadarCommModule.configure(options, replace(SHARED, source=source))


@pytest.mark.parametrize("variable", ["ADAPT_TEST_URL", "ADAPT_TEST_KEY", "ADAPT_TEST_CA"])
def test_a_run_that_sends_needs_its_api_settings_when_it_is_configured(monkeypatch, variable):
    export_api(monkeypatch)
    monkeypatch.delenv(variable)

    with pytest.raises(ValueError, match=variable):
        _configured("aws_nexrad", {"api": API})


def test_a_run_that_sends_needs_a_ca_file_it_can_read(monkeypatch, tmp_path):
    export_api(monkeypatch, ADAPT_TEST_CA=str(tmp_path / "gone.cer"))

    with pytest.raises(ValueError, match="ADAPT_TEST_CA"):
        _configured("aws_nexrad", {"api": API})


def test_a_run_with_its_api_settings_exported_is_configured_to_send(monkeypatch):
    export_api(monkeypatch)

    config = _configured("aws_nexrad", {"api": API})

    assert config.options.api is not None
    assert API_KEY not in config.options.model_dump_json()


def test_over_a_stored_run_nothing_is_sent(monkeypatch):
    for variable in ("ADAPT_TEST_URL", "ADAPT_TEST_KEY", "ADAPT_TEST_CA"):
        monkeypatch.delenv(variable, raising=False)  # none is needed: nothing is sent

    config = _configured("stored_run", {"api": API})

    assert config.options.api is None


@pytest.mark.parametrize("source", ["aws_nexrad", "stored_run"])
def test_the_run_is_configured_with_the_user_id_its_variable_holds(monkeypatch, source):
    monkeypatch.setenv("ADAPT_TEST_USER", "adapt_ops")

    assert _configured(source, {}).user_id == "adapt_ops"


@pytest.mark.parametrize("source", ["aws_nexrad", "stored_run"])
def test_a_run_whose_user_id_variable_is_unset_is_refused_dry_run_or_not(monkeypatch, source):
    monkeypatch.delenv("ADAPT_TEST_USER")

    with pytest.raises(ValueError, match="ADAPT_TEST_USER.*arm_radar_comm.radar.user_id_env"):
        _configured(source, {})


def test_the_command_carries_the_user_id_the_run_was_configured_with(monkeypatch):
    monkeypatch.setenv("ADAPT_TEST_USER", "adapt_ops")

    row = _row(ArmRadarCommModule().run(_context(_configured("aws_nexrad", {}))))

    assert json.loads(row["command_json"])["AutoTarget"]["user_id"] == "adapt_ops"
