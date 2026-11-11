# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""CLI dispatch: the command set, usage errors and --version.

The single-instance PID file is exercised through ``adapt run`` (test_run_checks,
test_run_live).
"""

import pytest

from adapt import __version__, cli

pytestmark = pytest.mark.unit


class TestMainDispatch:
    def test_no_command_exits_with_usage_error(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["adapt"])

        with pytest.raises(SystemExit) as exc:
            cli.main()

        assert exc.value.code == 2

    @pytest.mark.parametrize(
        "argv",
        [["tse", "run"], ["radar-command", "run"], ["run-nexrad"], ["postprocess"]],
    )
    def test_retired_commands_are_gone(self, monkeypatch, capsys, argv):
        # A pipeline file runs the pipeline, its consumers and its post-processing.
        monkeypatch.setattr("sys.argv", ["adapt", *argv])

        with pytest.raises(SystemExit) as exc:
            cli.main()

        assert exc.value.code == 2
        assert "invalid choice" in capsys.readouterr().err

    def test_version_prints_and_exits(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["adapt", "--version"])

        with pytest.raises(SystemExit) as exc:
            cli.main()

        assert exc.value.code == 0
        assert "adapt" in capsys.readouterr().out


def test_version_prints_the_version_and_the_install_path_on_two_lines(adapt, capsys):
    assert adapt("--version") == 0

    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == f"adapt {__version__}"
    assert lines[1].startswith("Installed at: ")
    assert len(lines) == 2
