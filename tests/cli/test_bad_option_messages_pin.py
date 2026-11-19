# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Pin: what a user reads when a pipeline file sets a bad option.

Two forms, one line per option, ``params.<key>.<option> — <why>``, under a
heading that names the file: a value outside the option's bounds, and an option
that does not exist. Read from ``adapt graph NAME``, which checks a file as a
run of it would and needs no store.
"""

import pytest
from tests.helpers.pipeline_files import write_pipeline

pytestmark = pytest.mark.unit


@pytest.fixture
def refusal(adapt, tmp_path, monkeypatch, capsys):
    """What ``adapt graph nexrad`` prints on stderr for a file with these ``params.global``."""

    def _refusal(**options) -> str:
        write_pipeline(
            tmp_path,
            "nexrad",
            run={"source": "aws_nexrad", "radar": "KTST", "mode": "realtime"},
            params={"global": options},
        )
        monkeypatch.chdir(tmp_path)
        adapt("graph", "nexrad")
        return capsys.readouterr().err

    return _refusal


def test_a_value_out_of_range_names_the_option_the_value_and_the_bound(refusal):
    message = refusal(max_scan_gap_minutes=0)

    assert "nexrad.yaml" in message.splitlines()[0]
    assert (
        "  • params.global.max_scan_gap_minutes — value 0 is out of range — "
        "Input should be greater than 0"
    ) in message.splitlines()


def test_an_unknown_option_is_named_as_unrecognized(refusal):
    message = refusal(no_such_option=1)

    assert "nexrad.yaml" in message.splitlines()[0]
    assert (
        "  • params.global.no_such_option — unrecognized option (value: 1) — not a valid "
        "'global' setting; remove it or check for a typo or a renamed field"
    ) in message.splitlines()
