# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Every wrong option of a pipeline file is told in one refusal.

``adapt run NAME`` and ``adapt graph NAME`` name the file once, then each wrong
option on its own line — the options of every listed module and of the
``global``, ``downloader`` and ``logging`` sections — then, per module with a
wrong option, the command that prints its options. Nobody has to fix one
option, run again and meet the next.
"""

import pytest
from tests.helpers.pipeline_files import write_pipeline

pytestmark = pytest.mark.unit

_REALTIME = {"source": "aws_nexrad", "radar": "KTST", "mode": "realtime"}
_OUT_OF_RANGE = "is out of range — Input should be greater than 0"


@pytest.fixture
def refusal(tmp_path, adapt, monkeypatch, capsys):
    """What ``adapt COMMAND case`` prints on stderr for a file with these ``params``,
    in an initialized store, after exiting with the configuration-error code."""
    root = tmp_path / "store"
    adapt("init", str(root))
    monkeypatch.chdir(root)

    def _refusal(command: str, **params) -> list[str]:
        path = write_pipeline(root, "case", run=_REALTIME, params=params)
        capsys.readouterr()
        assert adapt(command, "case") == 2
        lines = capsys.readouterr().err.splitlines()
        assert lines[0] == f"Invalid configuration (pipeline file {path}):"
        return lines[1:]

    return _refusal


@pytest.mark.parametrize("command", ["run", "graph"])
def test_wrong_options_of_two_modules_and_a_section_are_told_together(refusal, command):
    told = refusal(
        command,
        detection={"h_maxima": -1},
        tracking={"max_speed_ms": 0},
        **{"global": {"max_scan_gap_minutes": 0}},
    )

    assert told == [
        f"  • params.detection.h_maxima — value -1 {_OUT_OF_RANGE}",
        f"  • params.tracking.max_speed_ms — value 0 {_OUT_OF_RANGE}",
        f"  • params.global.max_scan_gap_minutes — value 0 {_OUT_OF_RANGE}",
        "The options of detection: adapt config --show detection",
        "The options of tracking: adapt config --show tracking",
    ]


@pytest.mark.parametrize("command", ["run", "graph"])
def test_wrong_options_of_all_three_sections_and_a_module_are_told_together(refusal, command):
    told = refusal(
        command,
        tracking={"max_speed_ms": 0},
        downloader={"latest_files": 0},
        logging={"level": "LOUD"},
        **{"global": {"max_scan_gap_minutes": 0}},
    )

    assert [line.split(" — ")[0] for line in told] == [
        "  • params.tracking.max_speed_ms",
        "  • params.downloader.latest_files",
        "  • params.global.max_scan_gap_minutes",
        "  • params.logging.level",
        "The options of tracking: adapt config --show tracking",
    ]


@pytest.mark.parametrize("command", ["run", "graph"])
def test_two_wrong_options_of_one_module_name_its_command_once(refusal, command):
    told = refusal(command, tracking={"max_speed_ms": 0, "no_such_option": 1})

    assert [line.split(" — ")[0] for line in told] == [
        "  • params.tracking.max_speed_ms",
        "  • params.tracking.no_such_option",
        "The options of tracking: adapt config --show tracking",
    ]


@pytest.mark.parametrize("command", ["run", "graph"])
def test_a_single_wrong_module_option_reads_as_it_always_did(refusal, command):
    assert refusal(command, detection={"h_maxima": -1}) == [
        f"  • params.detection.h_maxima — value -1 {_OUT_OF_RANGE}",
        "The options of detection: adapt config --show detection",
    ]


@pytest.mark.parametrize("command", ["run", "graph"])
def test_a_single_wrong_section_option_reads_as_it_always_did(refusal, command):
    assert refusal(command, **{"global": {"max_scan_gap_minutes": 0}}) == [
        f"  • params.global.max_scan_gap_minutes — value 0 {_OUT_OF_RANGE}",
    ]
