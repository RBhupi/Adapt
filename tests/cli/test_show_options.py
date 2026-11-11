# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt config --show NAME``: options with their defaults, as YAML ready to paste.

NAME is a module (it shows the params sections that hold its options, or its
own params when it owns them) or a params section. The output is lines to paste
under the file's ``params:`` line, which its first line names; pasted there, a
fragment changes nothing: it prints the defaults.
"""

from importlib.resources import files
from typing import Any

import pytest
import yaml
from pydantic import TypeAdapter

from adapt.configuration.pipeline_file import load_pipeline_file, param_section_names
from adapt.configuration.schemas.param import ParamConfig
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import catalog_module_names
from adapt.modules.radar_command import RadarCommandConfig
from adapt.modules.target_selection import TSEConfig

pytestmark = pytest.mark.unit

# Modules whose options are a packaged, commented rule, validated by these models.
RULES = {"target_selection": TSEConfig, "radar_command": RadarCommandConfig}
PARAMS_LINE = "# Paste these lines under the params: line of your pipeline file"
TSE_PRESET = files("adapt.modules.target_selection") / "presets" / "growing_isolated.yaml"
SANDBOX_PRESET = files("adapt.modules.radar_command") / "presets" / "sandbox.yaml"


@pytest.fixture
def show(adapt, capsys):
    """``adapt config --show NAME``'s output; fails unless the command succeeds."""

    def _show(name: str) -> str:
        capsys.readouterr()  # drop what earlier commands printed
        assert adapt("config", "--show", name) == 0
        return capsys.readouterr().out

    return _show


def _names() -> list[str]:
    return [*catalog_module_names(), *param_section_names()]


def _pasteable() -> list[str]:
    return [name for name in _names() if name != "output"]  # output has no live option


def _paste(text: str, fragment: str) -> str:
    """``text`` with ``fragment`` pasted under the line that its first line names."""
    key = fragment.splitlines()[0].split("under the ")[1].split(" line")[0]
    lines = text.splitlines(keepends=True)
    at = next(i for i, line in enumerate(lines) if line.startswith(key)) + 1
    return "".join([*lines[:at], fragment, *lines[at:]])


def _template(tmp_path, adapt, monkeypatch) -> str:
    monkeypatch.chdir(tmp_path)
    adapt("init")
    return (tmp_path / "nexrad.yaml").read_text()


def _load(tmp_path, text: str):
    path = tmp_path / "pasted.yaml"
    path.write_text(text)
    return load_pipeline_file(path)


def _at_their_defaults(pipeline) -> bool:
    """Whether the file's params leave every option at its default."""
    sections = {k: v for k, v in pipeline.params.items() if k in param_section_names()}
    as_json = TypeAdapter(dict[str, Any])
    modules = [m for m in pipeline.modules if m not in RULES]
    return ParamConfig.model_validate(sections) == ParamConfig() and all(
        as_json.dump_python({**registry.get(m).default_params(), **pipeline.params[m]}, mode="json")
        == as_json.dump_python(registry.get(m).default_params(), mode="json")
        for m in modules
        if m in pipeline.params
    )


def test_a_section_is_printed_to_paste_under_params_with_commented_defaults(show):
    out = show("segmenter")

    assert out.splitlines()[0] == PARAMS_LINE
    assert "  segmenter:" in out.splitlines()
    assert yaml.safe_load(out)["segmenter"]["method"] == "threshold"
    method = next(line for line in out.splitlines() if line.strip().startswith("method:"))
    comment = method.split("#", 1)[1]
    assert "conv_strat_raut | " in comment  # the allowed values


@pytest.mark.parametrize("name", _pasteable())
def test_every_fragment_is_entries_indented_under_the_line_its_first_line_names(name, show):
    lines = show(name).splitlines()

    assert lines[0] == PARAMS_LINE
    entries = [line for line in lines[1:] if line.strip() and not line.startswith("#")]
    assert entries
    assert all(line.startswith("  ") for line in entries)


def test_a_module_shows_the_sections_that_hold_its_options(show):
    out = show("ingest")

    assert set(yaml.safe_load(out)) == {"reader", "regridder"}
    assert "adapt config --show global" in out


def test_a_module_that_owns_its_options_shows_them_under_its_name(show):
    params = yaml.safe_load(show("cell_volume_stats"))["cell_volume_stats"]

    assert params["gap_tolerance_m"] == 500.0
    assert "reflectivity_var" not in params  # filled from the shared global section


def test_options_owned_by_the_run_section_are_not_offered(show):
    downloader = yaml.safe_load(show("downloader"))["downloader"]

    assert not {"radar", "mode", "start_time", "end_time"} & set(downloader)


def test_logging_offers_every_option_a_run_reads(show):
    logging = yaml.safe_load(show("logging"))["logging"]

    assert logging == {
        "level": "INFO",
        "console_level": "WARNING",
        "console_logs": True,
        "json_logs": False,
        "enabled": True,
        "traces": True,
        "metrics": True,
    }


def test_a_section_with_only_obsolete_options_says_it_has_none(show):
    out = show("output")

    assert yaml.safe_load(out) is None  # nothing to paste
    assert "params.output has no options" in out


def test_target_selection_prints_its_packaged_rule_with_its_comments(show):
    out = show("target_selection")
    preset = TSE_PRESET.read_text("utf-8")

    assert out.splitlines()[0] == PARAMS_LINE
    expected = yaml.safe_load(preset)["target_selection"]
    assert yaml.safe_load(out)["target_selection"] == expected
    rationale = next(line for line in preset.splitlines() if "decided at the 2nd" in line)
    assert f"  {rationale}" in out.splitlines()


def test_radar_command_prints_the_sandbox_preset_a_dry_run(show):
    section = yaml.safe_load(show("radar_command"))["radar_command"]

    assert section == yaml.safe_load(SANDBOX_PRESET.read_text("utf-8"))["radar_command"]
    assert "api" not in section


def test_an_unknown_name_lists_every_valid_name_by_kind(adapt, capsys):
    assert adapt("config", "--show", "bogus") == 2

    err = capsys.readouterr().err
    assert "bogus" in err
    for kind in ("modules", "params sections"):
        assert kind in err
    for name in _names():
        assert name in err


@pytest.mark.parametrize("name", _pasteable())
def test_pasting_the_fragment_under_its_line_changes_nothing(
    name, show, adapt, tmp_path, monkeypatch
):
    template = _template(tmp_path, adapt, monkeypatch)
    template = template.replace(f"  # - {name}", f"  - {name}")  # its params need it listed

    pipeline = _load(tmp_path, _paste(template, show(name)))

    if name in RULES:
        RULES[name].model_validate(pipeline.params[name])
        assert pipeline.params[name] == registry.get(name).default_params()
    else:
        assert _at_their_defaults(pipeline)


def test_two_fragments_pasted_one_after_the_other_under_params_load(
    show, adapt, tmp_path, monkeypatch
):
    template = _template(tmp_path, adapt, monkeypatch)
    # params: is the template's last line, so appending pastes under it (show NAME >> file).
    pipeline = _load(tmp_path, template + show("segmenter") + show("tracker"))

    assert set(pipeline.params) == {"segmenter", "tracker"}
    assert _at_their_defaults(pipeline)
