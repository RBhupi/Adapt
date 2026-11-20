# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt config --params --file FILE [NAME...]``: add the options FILE lacks.

The modules NAME (none: every module FILE lists) get, under ``params:``, every
default option they lack: a module FILE does not give, as ``--params NAME``
prints it; a module it gives, the options missing from each part it gives.
Every line FILE has stays as it is, so the resolved options do not change and
a second run changes nothing.
"""

import pytest

from adapt.configuration.pipeline_file import load_pipeline_file
from adapt.configuration.pipeline_run import configured_modules
from adapt.configuration.schemas.module_resolver import module_defaults, module_options

pytestmark = pytest.mark.unit

_HEAD = """\
# my pipeline
run:
  source: aws_nexrad
  radar: KLOT
  mode: realtime

modules:
  - ingest
  - detection
  - projection
  - analysis
  - tracking
  # - target_selection

params:
"""

_EDITED = (
    _HEAD
    + """\
  # my note on projection
  projection:
    max_flow_magnitude: 25.0   # mine
    flow_params:
      levels: 5   # tuned
  tracking:
    max_speed_ms: 35  # faster storms today
# trailing comment
"""
)


@pytest.fixture
def pipeline_file(tmp_path, monkeypatch):
    """Write ``text`` to ``./case.yaml`` in a fresh folder; return its path."""
    monkeypatch.chdir(tmp_path)

    def _write(text: str):
        path = tmp_path / "case.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    return _write


@pytest.fixture
def add(adapt, capsys):
    """Run ``adapt config --params --file PATH NAME...``; return its exit code and output."""

    def _add(path, *names: str) -> tuple[int | str, str]:
        capsys.readouterr()
        code = adapt("config", "--params", "--file", str(path), *names)
        out = capsys.readouterr()
        return code, out.out + out.err

    return _add


def _resolved(path) -> dict:
    pipeline = load_pipeline_file(path)
    return {
        name: module_options(name, pipeline.params.get(name, {}), source="test")
        for name in pipeline.modules
    }


def _kept_in_order(old: str, new: str) -> bool:
    """Whether every line of ``old`` is in ``new``, in the same order."""
    lines = iter(new.splitlines())
    return all(line in lines for line in old.splitlines())


def test_several_named_modules_are_added_with_their_defaults(pipeline_file, add):
    path = pipeline_file(_HEAD)

    code, out = add(path, "tracking", "detection")

    assert code == 0
    assert "Added the missing options of tracking, detection" in out
    text = path.read_text()
    assert text.startswith(_HEAD + "  # tracking: ")
    assert "\n\n\n  # detection: " in text
    params = load_pipeline_file(path).params
    assert list(params) == ["tracking", "detection"]
    assert params["tracking"] == module_defaults("tracking")


def test_edits_and_comments_are_kept_and_the_rest_is_added(pipeline_file, add):
    path = pipeline_file(_EDITED)
    before = _resolved(path)

    assert add(path, "tracking", "projection")[0] == 0

    text = path.read_text()
    assert _kept_in_order(_EDITED, text)
    params = load_pipeline_file(path).params
    assert params["tracking"] == {**module_defaults("tracking"), "max_speed_ms": 35}
    assert set(params["projection"]) == set(module_defaults("projection"))
    assert params["projection"]["max_flow_magnitude"] == 25.0
    assert _resolved(path) == before


def test_a_nested_part_keeps_its_settings_and_gains_the_ones_it_lacks(pipeline_file, add):
    path = pipeline_file(_EDITED)

    add(path, "projection")

    flow = load_pipeline_file(path).params["projection"]["flow_params"]
    assert flow == {**module_defaults("projection")["flow_params"], "levels": 5}
    lines = path.read_text().splitlines()
    at = lines.index("      levels: 5   # tuned")
    assert lines[at + 1].startswith("      pyr_scale: 0.5  # ")  # inside flow_params


def test_an_option_given_whole_is_not_extended(pipeline_file, add):
    # score.weights is a mapping of free keys: given, it replaces the default whole.
    given = "  target_selection:\n    score:\n      weights: {n_scans: 1.0}\n"
    path = pipeline_file(_HEAD + given)

    add(path, "target_selection")

    score = load_pipeline_file(path).params["target_selection"]["score"]
    assert score["weights"] == {"n_scans": 1.0}
    assert "site_preference" in score
    assert set(load_pipeline_file(path).params["target_selection"]) == set(
        module_defaults("target_selection")
    )


def test_a_module_the_file_does_not_list_may_get_its_options_and_does_not_run(pipeline_file, add):
    path = pipeline_file(_HEAD)

    assert add(path, "target_selection")[0] == 0

    pipeline = load_pipeline_file(path)
    assert pipeline.params["target_selection"] == module_defaults("target_selection")
    assert "target_selection" not in pipeline.modules
    ran = {type(node).name for node in configured_modules(pipeline, path)}
    assert ran == {"ingest", "detection", "projection", "analysis", "tracking"}


def test_without_names_every_listed_module_and_only_those_gain_options(pipeline_file, add):
    path = pipeline_file(_EDITED)

    assert add(path)[0] == 0

    params = load_pipeline_file(path).params
    assert list(params) == ["projection", "tracking", "ingest", "detection", "analysis"]
    for name in params:
        assert set(params[name]) == set(module_defaults(name)), name


@pytest.mark.parametrize("names", [(), ("tracking", "projection", "target_selection")])
def test_a_second_run_changes_nothing(pipeline_file, add, names):
    path = pipeline_file(_EDITED)
    add(path, *names)
    once = path.read_text()

    code, out = add(path, *names)

    assert code == 0
    assert path.read_text() == once
    assert "nothing changed" in out


def test_a_file_written_with_every_option_gains_nothing(adapt, add, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    adapt("config", "--nexrad", "--params")
    path = tmp_path / "nexrad.yaml"
    written = path.read_text()

    assert add(path)[0] == 0
    assert add(path, "target_selection", "arm_radar_comm", "xlma_stat")[0] == 0

    assert path.read_text() == written


@pytest.mark.parametrize(
    ("names", "told"),
    [
        (("tracking", "trackin"), "Unknown module(s) trackin"),
        (("global",), "Unknown module(s) global"),
    ],
    ids=["unknown", "section"],
)
def test_a_name_that_is_not_a_module_is_refused_and_nothing_is_written(
    pipeline_file, add, names, told
):
    path = pipeline_file(_EDITED)

    code, out = add(path, *names)

    assert code == 2
    assert told in out
    assert path.read_text() == _EDITED


def test_a_missing_file_is_refused(add, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    code, out = add(tmp_path / "nope.yaml", "tracking")

    assert code == 2
    assert "No pipeline file" in out
    assert not (tmp_path / "nope.yaml").exists()


@pytest.mark.parametrize(
    ("params", "told"),
    [
        ("  tracking: [unclosed\n", "is not valid YAML"),
        ("  trackin:\n    max_speed_ms: 35\n", "unknown params key 'trackin'"),
        ("  tracking: {max_speed_ms: 35}\n", "params.tracking is not written one entry per line"),
    ],
    ids=["malformed", "invalid", "flow-style"],
)
def test_a_file_that_cannot_take_options_is_refused_and_kept(pipeline_file, add, params, told):
    path = pipeline_file(_HEAD + params)

    code, out = add(path, "tracking")

    assert code == 2
    assert told in out
    assert path.read_text() == _HEAD + params
