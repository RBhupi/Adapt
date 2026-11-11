# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt init``: the store plus one pipeline file per template; never twice."""

import pytest

from adapt.configuration.pipeline_file import load_pipeline_file

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("isolated_catalog")]


def test_init_creates_the_store_and_a_pipeline_file_and_reports_the_root(tmp_path, adapt, capsys):
    root = tmp_path / "case"

    assert adapt("init", str(root)) == 0

    assert {p.name for p in root.iterdir()} == {"registry.db", "logs", "collections", "nexrad.yaml"}
    assert str(root) in capsys.readouterr().out


def test_init_opens_with_the_attribution_banner(tmp_path, adapt, capsys):
    adapt("init", str(tmp_path / "case"))

    assert capsys.readouterr().out.startswith("ARM Adapt v")


def test_the_written_pipeline_file_is_the_nexrad_template_and_loads(tmp_path, adapt, monkeypatch):
    root = tmp_path / "case"
    adapt("init", str(root))
    monkeypatch.chdir(tmp_path)
    adapt("config", "nexrad")

    assert (root / "nexrad.yaml").read_text() == (tmp_path / "nexrad.yaml").read_text()
    assert load_pipeline_file(root / "nexrad.yaml").core


def test_init_defaults_to_the_current_directory(tmp_path, adapt, monkeypatch):
    monkeypatch.chdir(tmp_path)

    adapt("init")

    assert (tmp_path / "registry.db").exists()
    assert (tmp_path / "nexrad.yaml").exists()


def test_init_twice_fails_and_leaves_the_pipeline_file_alone(tmp_path, adapt, capsys):
    root = tmp_path / "case"
    adapt("init", str(root))
    (root / "nexrad.yaml").write_text("# edited\n")

    assert adapt("init", str(root)) == 1

    assert "already initialized" in capsys.readouterr().err
    assert (root / "nexrad.yaml").read_text() == "# edited\n"


def test_init_keeps_a_pipeline_file_that_already_exists(tmp_path, adapt, capsys):
    root = tmp_path / "case"
    root.mkdir()
    (root / "nexrad.yaml").write_text("# mine\n")

    assert adapt("init", str(root)) == 0

    assert (root / "registry.db").exists()
    assert (root / "nexrad.yaml").read_text() == "# mine\n"
    assert "nexrad.yaml" in capsys.readouterr().out


def test_init_twice_gives_the_next_step_and_no_banner(tmp_path, adapt, capsys):
    root = tmp_path / "case"
    adapt("init", str(root))
    capsys.readouterr()

    assert adapt("init", str(root)) == 1

    out, err = capsys.readouterr()
    assert "adapt config" in err
    assert out == ""


def test_init_on_a_file_is_refused_cleanly(tmp_path, adapt, capsys):
    path = tmp_path / "notes.txt"
    path.write_text("notes")

    assert adapt("init", str(path)) == 1

    assert "notes.txt" in capsys.readouterr().err


def test_init_inside_a_store_is_refused_naming_the_store(tmp_path, adapt, capsys):
    root = tmp_path / "case"
    adapt("init", str(root))
    capsys.readouterr()

    assert adapt("init", str(root / "collections" / "sub")) == 1

    assert str(root) in capsys.readouterr().err
    assert not (root / "collections" / "sub").exists()
