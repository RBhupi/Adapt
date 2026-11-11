# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt config NAME``: write ``./NAME.yaml`` from the template, asking before overwriting."""

import pytest

from adapt.configuration.pipeline_file import load_pipeline_file

pytestmark = pytest.mark.unit


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_writes_the_short_pipeline_file_in_the_current_directory(cwd, adapt, capsys):
    assert adapt("config", "nexrad") == 0

    path = cwd / "nexrad.yaml"
    assert path.read_text().startswith("# Adapt pipeline file. Run it with:  adapt run ")
    assert load_pipeline_file(path).modules
    assert "adapt run nexrad" in capsys.readouterr().out


@pytest.mark.parametrize(("answer", "kept"), [("y", False), ("n", True), ("", True)])
def test_asks_before_overwriting(cwd, adapt, monkeypatch, capsys, answer, kept):
    path = cwd / "nexrad.yaml"
    path.write_text("# mine\n")
    monkeypatch.setattr("builtins.input", lambda prompt: answer)

    assert adapt("config", "nexrad") == 0

    assert (path.read_text() == "# mine\n") is kept
    if kept:
        assert "Aborted" in capsys.readouterr().out


def test_unknown_template_lists_the_available_ones_and_writes_nothing(cwd, adapt, capsys):
    assert adapt("config", "bogus") == 2

    assert "nexrad" in capsys.readouterr().err
    assert not (cwd / "bogus.yaml").exists()


def test_needs_a_name_or_show(cwd, adapt):
    assert adapt("config") == 2


def test_name_and_show_are_exclusive(cwd, adapt):
    assert adapt("config", "nexrad", "--show", "tracking") == 2


@pytest.mark.parametrize(
    ("interrupt", "code"), [(EOFError, 0), (KeyboardInterrupt, 1)], ids=["eof", "ctrl-c"]
)
def test_an_interrupted_overwrite_prompt_aborts_cleanly(
    cwd, adapt, monkeypatch, capsys, interrupt, code
):
    path = cwd / "nexrad.yaml"
    path.write_text("# mine\n")

    def _interrupt(prompt):
        raise interrupt

    monkeypatch.setattr("builtins.input", _interrupt)

    assert adapt("config", "nexrad") == code

    assert path.read_text() == "# mine\n"
    assert "Aborted" in capsys.readouterr().out


def test_outside_a_store_the_next_step_is_adapt_init(cwd, adapt, capsys):
    assert adapt("config", "nexrad") == 0

    out = capsys.readouterr().out
    assert "adapt init" in out
    assert "this folder (an Adapt store)" not in out
