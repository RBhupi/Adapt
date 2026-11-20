# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt config --nexrad [--params]``: write ``./nexrad.yaml`` from the template.

The file is never written over an existing one.

With ``--params`` the file also gives every catalog module's options at their
defaults under ``params:``. ``adapt config`` needs a template or ``--params``.
"""

import pytest

from adapt.configuration.pipeline_file import load_pipeline_file
from adapt.configuration.pipeline_template import PIPELINE_TEMPLATES, pipeline_template
from adapt.execution.pipeline_builder import catalog_module_names

pytestmark = pytest.mark.unit


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_writes_the_short_pipeline_file_in_the_current_directory(cwd, adapt, capsys):
    assert adapt("config", "--nexrad") == 0

    path = cwd / "nexrad.yaml"
    assert path.read_text() == pipeline_template("nexrad")
    assert load_pipeline_file(path).params == {}
    assert "adapt run nexrad" in capsys.readouterr().out


def test_with_params_every_catalog_module_gets_its_options_under_its_heading(cwd, adapt):
    assert adapt("config", "--nexrad", "--params") == 0

    text = (cwd / "nexrad.yaml").read_text()
    assert text.startswith(pipeline_template("nexrad"))
    pipeline = load_pipeline_file(cwd / "nexrad.yaml")
    assert list(pipeline.params) == catalog_module_names()
    assert pipeline.modules == PIPELINE_TEMPLATES["nexrad"].modules  # the others stay off
    for name in catalog_module_names():
        assert f"\n  # {name}: " in text
    assert "\n\n\n  # detection: " in text  # two empty lines between modules


@pytest.mark.parametrize("flags", [(), ("--params",)])
def test_an_existing_file_is_refused_and_kept(cwd, adapt, capsys, flags):
    path = cwd / "nexrad.yaml"
    path.write_text("# mine\n")

    assert adapt("config", "--nexrad", *flags) == 2

    assert path.read_text() == "# mine\n"
    err = capsys.readouterr().err
    assert "already exists" in err
    assert "adapt config --params --file nexrad.yaml" in err


@pytest.mark.parametrize(
    "args",
    [("--nexrad", "tracking"), ("--nexrad", "--params", "--file", "nexrad.yaml")],
    ids=["names", "file"],
)
def test_the_template_takes_no_module_names_or_file(cwd, adapt, capsys, args):
    assert adapt("config", *args) == 2

    assert "takes no module names or --file" in capsys.readouterr().err
    assert not (cwd / "nexrad.yaml").exists()


@pytest.mark.parametrize("args", [(), ("tracking",), ("--file", "nexrad.yaml")])
def test_without_a_template_params_is_needed(cwd, adapt, capsys, args):
    assert adapt("config", *args) == 2

    assert "adapt config needs --params" in capsys.readouterr().err


def test_params_alone_needs_names_or_a_file(cwd, adapt, capsys):
    assert adapt("config", "--params") == 2

    assert "--params needs module names to print, or --file FILE" in capsys.readouterr().err


def test_outside_a_store_the_next_step_is_adapt_init(cwd, adapt, capsys):
    assert adapt("config", "--nexrad") == 0

    out = capsys.readouterr().out
    assert "adapt init" in out
    assert "this folder (an Adapt store)" not in out
