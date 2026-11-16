# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""RadarCommandModule: one scan's selection -> the command recorded (and sent)."""

import json
import logging
from types import SimpleNamespace

import pandas as pd
import pytest

from adapt.contracts import check_radar_commands
from adapt.execution.nodes import radar_command as node
from adapt.execution.nodes.radar_command import RadarCommandModule
from adapt.modules.radar_command import RadarCommandConfig
from adapt.modules.radar_command.transport import NotSent, SendError
from tests.helpers import radar_store
from tests.helpers.pipeline_files import API, API_KEY, API_URL, RADAR_COMMAND, export_api

pytestmark = pytest.mark.unit

_BAND = {"altitude_min_m": 500.0, "altitude_max_m": 12000.0}


def _config(**parts) -> RadarCommandConfig:
    return RadarCommandConfig.model_validate({**RADAR_COMMAND, **parts})


def _selection(cell_uid: str | None) -> pd.DataFrame:
    return pd.DataFrame([{"config_hash": "0123456789abcdef", "cell_uid": cell_uid}])


def _context(config, cell_uid=radar_store.UID_A, scan=1, **overrides) -> dict:
    outputs = radar_store.scan_outputs(scan)
    return {
        "radar_command_config": config,
        "selection": _selection(cell_uid),
        "analysis_ds": outputs["analysis_ds"],
        "cell_volume_stats_rows": outputs["cell_volume_stats_rows"],
        "run_id": "run-1",
        "scan_id": f"scan-{scan}",
        "scan_time": radar_store.SCAN_TIMES[scan],
        "radar_command_state": None,  # nothing published yet
        **overrides,
    }


def _row(result: dict) -> pd.Series:
    check_radar_commands(result["radar_commands"])
    return result["radar_commands"].iloc[0]


def test_a_selected_cell_gets_a_published_command():
    row = _row(RadarCommandModule().run(_context(_config())))

    assert row["outcome"] == "PUBLISHED"
    assert row["cell_uid"] == radar_store.UID_A
    assert row["selection_config_hash"] == "0123456789abcdef"
    assert row["vertical_source"] == "cell_volume_stats"
    assert row["n_entries"] == 12
    (payload,) = json.loads(row["command_json"]).values()
    assert payload["target_id"] == row["plan_id"]
    assert (payload["scan_pattern"], payload["user_id"]) == ("ppi_sector", "operator")


def test_no_selected_cell_is_recorded_as_no_target():
    row = _row(RadarCommandModule().run(_context(_config(), cell_uid=None)))

    assert row["outcome"] == "NO_TARGET"
    assert row["plan_id"] is None and row["command_json"] is None


def test_a_cell_that_cannot_be_located_is_recorded_with_why():
    row = _row(RadarCommandModule().run(_context(_config(), cell_uid="uid-zz")))

    assert row["outcome"] == "TARGET_UNAVAILABLE"
    assert "uid-zz" in row["detail"]


def test_the_same_plan_is_not_published_twice_by_one_run():
    """What was published is carried from scan to scan as the module's state."""
    first_result = RadarCommandModule().run(_context(_config()))
    first = _row(first_result)
    published = first_result["radar_command_state"]

    again = _row(RadarCommandModule().run(_context(_config(), radar_command_state=published)))

    assert published == frozenset({first["plan_id"]})
    assert again["outcome"] == "DUPLICATE"
    assert again["plan_id"] == first["plan_id"]


def test_a_scan_without_a_command_leaves_the_state_as_it_was():
    result = RadarCommandModule().run(
        _context(_config(), cell_uid=None, radar_command_state=frozenset({"earlier-plan"}))
    )

    assert result["radar_command_state"] == frozenset({"earlier-plan"})


def test_a_fixed_band_needs_no_measured_extent():
    config = _config(extent={"horizontal_buffer_km": 1.0, "vertical_band": _BAND})
    context = _context(config)
    del context["cell_volume_stats_rows"]

    row = _row(RadarCommandModule().run(context))

    assert "cell_volume_stats_rows" not in RadarCommandModule.inputs_for(config)
    assert "cell_volume_stats_rows" in RadarCommandModule.inputs_for(_config())
    assert (row["outcome"], row["vertical_source"]) == ("PUBLISHED", "config")


def test_a_configured_api_is_sent_the_command_with_the_key(monkeypatch):
    sent = []
    export_api(monkeypatch)
    monkeypatch.setattr(
        node, "send", lambda command_json, target: sent.append((command_json, target)) or "queued"
    )

    row = _row(RadarCommandModule().run(_context(_config(api=API))))

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

    RadarCommandModule().run(_context(_config(api=API)))

    assert list(json.loads(sent[0])) == ["AutoTarget"]


def test_an_api_that_refuses_is_recorded_and_does_not_raise(monkeypatch):
    def refuse(command_json, target):
        raise SendError("https://radar.example.test/publish/command answered HTTP 503: down")

    export_api(monkeypatch)
    monkeypatch.setattr(node, "send", refuse)

    row = _row(RadarCommandModule().run(_context(_config(api=API))))

    assert row["outcome"] == "NOT_ACKNOWLEDGED"
    assert "HTTP 503" in row["detail"]
    assert row["command_json"] is not None  # what would have been sent is kept


def _console_line(caplog) -> str:
    (record,) = [r for r in caplog.records if "radar_command:" in r.getMessage()]
    assert record.console is True
    return record.getMessage()


def test_a_dry_run_says_on_the_console_that_nothing_was_sent(caplog):
    caplog.set_level(logging.INFO)

    row = _row(RadarCommandModule().run(_context(_config())))

    line = _console_line(caplog)
    assert line == (
        f"radar_command: command {row['plan_id']} written for cell {radar_store.UID_A} "
        "(dry run, nothing sent)"
    )


def test_a_command_sent_and_acknowledged_gets_two_ticks_on_the_console(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    export_api(monkeypatch)
    monkeypatch.setattr(node, "send", lambda command_json, target: "HTTP 200: published, and more")

    row = _row(RadarCommandModule().run(_context(_config(api=API))))

    assert _console_line(caplog) == (
        f"✓✓ radar_command: command {row['plan_id']} sent for cell {radar_store.UID_A} "
        "(queued by the radar API)"
    )


def test_a_command_sent_but_not_acknowledged_gets_a_tick_then_a_cross(monkeypatch, caplog):
    def refuse(command_json, target):
        raise SendError("HTTP 401: " + "no such key " * 40)

    caplog.set_level(logging.INFO)
    export_api(monkeypatch)
    monkeypatch.setattr(node, "send", refuse)

    row = _row(RadarCommandModule().run(_context(_config(api=API))))

    line = _console_line(caplog)
    assert row["outcome"] == "NOT_ACKNOWLEDGED"
    assert line.startswith(f"✓✗ radar_command: command {row['plan_id']} not acknowledged for cell")
    assert "HTTP 401: no such key" in line
    assert len(line) < 200 < len(row["detail"])  # the whole answer is in the table


def test_a_command_that_did_not_go_out_gets_a_cross_and_no_second_mark(monkeypatch, caplog):
    def unreachable(command_json, target):
        raise NotSent("unreachable: connection refused")

    caplog.set_level(logging.INFO)
    export_api(monkeypatch)
    monkeypatch.setattr(node, "send", unreachable)

    row = _row(RadarCommandModule().run(_context(_config(api=API))))

    assert row["outcome"] == "SEND_FAILED"
    assert _console_line(caplog) == (
        f"✗– radar_command: command {row['plan_id']} not sent for cell {radar_store.UID_A}"
        ": unreachable: connection refused"
    )


@pytest.mark.parametrize("failure", [NotSent("unreachable"), SendError("HTTP 500: down")])
def test_a_command_that_failed_is_not_sent_again_at_the_next_scan(monkeypatch, failure):
    def fail(command_json, target):
        raise failure

    export_api(monkeypatch)
    monkeypatch.setattr(node, "send", fail)
    first = RadarCommandModule().run(_context(_config(api=API)))

    again = _row(
        RadarCommandModule().run(
            _context(_config(api=API), radar_command_state=first["radar_command_state"])
        )
    )

    assert again["outcome"] == "DUPLICATE"


def test_api_settings_lost_during_the_run_are_recorded_and_do_not_raise(monkeypatch):
    export_api(monkeypatch)
    config = RadarCommandModule.build_config(_internal("aws_nexrad", {**RADAR_COMMAND, "api": API}))
    monkeypatch.delenv("ADAPT_TEST_CA")

    row = _row(RadarCommandModule().run(_context(config)))

    assert row["outcome"] == "SEND_FAILED"
    assert "ADAPT_TEST_CA" in row["detail"]
    assert row["command_json"] is not None


def _internal(source: str, params: dict):
    """As much of a run's config as build_config reads."""
    return SimpleNamespace(source=source, module_params={"radar_command": params})


@pytest.mark.parametrize("variable", ["ADAPT_TEST_URL", "ADAPT_TEST_KEY", "ADAPT_TEST_CA"])
def test_a_run_that_sends_needs_its_api_settings_when_it_is_configured(monkeypatch, variable):
    export_api(monkeypatch)
    monkeypatch.delenv(variable)

    with pytest.raises(ValueError, match=variable):
        RadarCommandModule.build_config(_internal("aws_nexrad", {"api": API}))


def test_a_run_that_sends_needs_a_ca_file_it_can_read(monkeypatch, tmp_path):
    export_api(monkeypatch, ADAPT_TEST_CA=str(tmp_path / "gone.cer"))

    with pytest.raises(ValueError, match="ADAPT_TEST_CA"):
        RadarCommandModule.build_config(_internal("aws_nexrad", {"api": API}))


def test_a_run_with_its_api_settings_exported_is_configured_to_send(monkeypatch):
    export_api(monkeypatch)

    config = RadarCommandModule.build_config(_internal("aws_nexrad", {"api": API}))

    assert config.api is not None
    assert API_KEY not in config.model_dump_json()


def test_over_a_stored_run_nothing_is_sent(monkeypatch):
    for variable in ("ADAPT_TEST_URL", "ADAPT_TEST_KEY", "ADAPT_TEST_CA"):
        monkeypatch.delenv(variable, raising=False)  # none is needed: nothing is sent

    config = RadarCommandModule.build_config(_internal("stored_run", {"api": API}))

    assert config.api is None


def test_the_files_parts_are_laid_over_the_packaged_options():
    config = RadarCommandModule.build_config(
        _internal("aws_nexrad", {"planning": {"horizon_minutes": 6}})
    )
    packaged = RadarCommandModule.default_params()

    assert config.planning.horizon_minutes == 6
    assert config.planning.interval_seconds == packaged["planning"]["interval_seconds"]
    assert config.radar.scan_pattern == packaged["radar"]["scan_pattern"]
