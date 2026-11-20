# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt config --params NAME...``: options with their defaults, as YAML ready to paste.

NAME is a module (it shows its options under its own name) or a params
section; several may be named at once. The output is lines to paste under the
file's ``params:`` line, which its first line names; pasted there, it changes
nothing: it prints the defaults.
"""

from importlib.resources import files

import pytest
import yaml

from adapt.configuration.pipeline_file import load_pipeline_file, param_section_models
from adapt.configuration.schemas.module_resolver import module_options
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import catalog_module_names
from adapt.modules.analysis.config import ALWAYS_EXCLUDED, AnalysisOptions
from adapt.modules.arm_radar_comm import RadarCommandConfig
from adapt.modules.detection.config import DetectionOptions
from adapt.modules.ingest.config import IngestOptions
from adapt.modules.projection.config import ProjectionOptions
from adapt.modules.target_selection import TSEConfig
from adapt.modules.tracking.config import TrackingOptions

pytestmark = pytest.mark.unit

# Modules whose options are a packaged, commented rule, validated by these models.
RULES = {"target_selection": TSEConfig, "arm_radar_comm": RadarCommandConfig}
PARAMS_LINE = "# Paste these lines under the params: line of your pipeline file"
TSE_PRESET = files("adapt.modules.target_selection") / "presets" / "growing_isolated.yaml"
SANDBOX_PRESET = files("adapt.modules.arm_radar_comm") / "presets" / "sandbox.yaml"


@pytest.fixture
def show(adapt, capsys):
    """``adapt config --params NAME...``'s output; fails unless the command succeeds."""

    def _show(*names: str) -> str:
        capsys.readouterr()  # drop what earlier commands printed
        assert adapt("config", "--params", *names) == 0
        return capsys.readouterr().out

    return _show


def _names() -> list[str]:
    return [*catalog_module_names(), *param_section_models()]


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
    sections = param_section_models()
    modules = [m for m in pipeline.modules if m not in RULES]
    return all(
        value == sections[key].model_fields[option].default
        for key in pipeline.params
        if key in sections
        for option, value in pipeline.params[key].items()
    ) and all(
        module_options(m, pipeline.params[m], source="the pasted file")
        == module_options(m, {}, source="its defaults")
        for m in modules
        if m in pipeline.params
    )


def test_a_section_is_printed_to_paste_under_params_with_commented_defaults(show):
    out = show("logging")

    assert out.splitlines()[0] == PARAMS_LINE
    assert "  logging:" in out.splitlines()
    assert yaml.safe_load(out)["logging"]["level"] == "INFO"
    level = next(line for line in out.splitlines() if line.strip().startswith("level:"))
    comment = level.split("#", 1)[1]
    assert "DEBUG | " in comment  # the allowed values


@pytest.mark.parametrize("name", _names())
def test_every_fragment_is_entries_indented_under_the_line_its_first_line_names(name, show):
    lines = show(name).splitlines()

    assert lines[0] == PARAMS_LINE
    entries = [line for line in lines[1:] if line.strip() and not line.startswith("#")]
    assert entries
    assert all(line.startswith("  ") for line in entries)


@pytest.mark.parametrize("name", [name for name in catalog_module_names() if name not in RULES])
def test_a_modules_header_names_the_command_that_shows_the_shared_options(name, show):
    header = show(name).splitlines()[1]

    assert header == (
        f"  # {name}: {registry.get(name).module_summary()}. "
        "Options shared by every module: adapt config --params global"
    )


def test_ingest_shows_every_option_under_its_own_name(show):
    lines = show("ingest").splitlines()

    assert "  ingest:" in lines
    assert "  reader:" not in lines
    assert "  regridder:" not in lines
    shown = yaml.safe_load("\n".join(lines))
    assert shown == {"ingest": IngestOptions().model_dump(mode="json")}
    assert list(shown["ingest"]) == list(IngestOptions.model_fields)


def test_ingest_shows_each_option_with_its_meaning_and_its_choices(show):
    lines = show("ingest").splitlines()

    assert lines[lines.index("  ingest:") + 1 :] == [
        "    file_format: nexrad_archive  # Radar file format to read; only nexrad_archive "
        "(NEXRAD Level-II) is supported: nexrad_archive",
        "    field_map: {}  # Source-to-canonical variable renames applied once at ingest, such "
        "as {corrected_reflectivity: reflectivity}; empty means no renames",
        "    fields: []  # Canonical variable names to keep after renaming; empty keeps every "
        "field the source file provides",
        "    grid_shape: [41, 301, 301]  # Grid points (nz, ny, nx) of the Cartesian grid each "
        "volume is regridded onto",
        "    grid_limits:  # Grid extent ((z_min, z_max), (y_min, y_max), (x_min, x_max)) in "
        "metres from the radar",
        "      - [0.0, 20000.0]",
        "      - [-150000.0, 150000.0]",
        "      - [-150000.0, 150000.0]",
        "    roi_func: dist_beam  # Py-ART radius-of-influence function: dist_beam grows the "
        "radius with range and beam size, dist with range only: dist_beam | dist",
        "    min_radius: 1750.0  # Smallest radius of influence (m) when regridding",
        "    weighting_function: cressman  # Weighting of the gates within the radius of "
        "influence when regridding: cressman | barnes | nearest",
    ]


def test_a_module_that_owns_its_options_shows_them_under_its_name(show):
    params = yaml.safe_load(show("cell_volume_stats"))["cell_volume_stats"]

    assert params["gap_tolerance_m"] == 500.0
    assert "reflectivity_var" not in params  # filled from the shared global section


def test_detection_shows_every_option_under_its_own_name_with_its_comment(show):
    lines = show("detection").splitlines()

    assert "  detection:" in lines
    entries = lines[lines.index("  detection:") + 1 :]
    for at, line in enumerate(entries):
        if line.strip().startswith("#"):
            continue
        if line.endswith(":"):  # a method's parameters: their comment is the line above
            assert entries[at - 1].strip().startswith("# Parameters of the "), line
        else:
            assert "  # " in line, line
    shown = yaml.safe_load("\n".join(lines))["detection"]
    assert set(shown) == set(DetectionOptions.model_fields)
    for name, value in shown.items():
        if name.endswith("_params"):
            block = DetectionOptions.model_fields[name].annotation
            assert set(value) == set(block.model_fields), name


def test_detection_shows_the_meaning_and_the_choices_of_an_option(show):
    lines = show("detection").splitlines()

    assert "    h_maxima: 5.0  # h-maxima height for cell seeding (dBZ)" in lines
    assert "    # Parameters of the threshold method" in lines
    assert (
        "      threshold: 35.0  # Reflectivity threshold in dBZ; cells have reflectivity above it"
        in lines
    )
    method = next(line for line in lines if line.strip().startswith("method:"))
    assert method.endswith(
        ": conv_strat_raut | conv_strat_yuter | feature_detection | steiner_conv_strat | threshold"
    )


def test_projection_shows_every_option_under_its_own_name_with_its_comment(show):
    lines = show("projection").splitlines()

    assert "  projection:" in lines
    assert "  projector:" not in lines
    entries = lines[lines.index("  projection:") + 1 :]
    assert entries[entries.index("    flow_params:") - 1] == (
        "    # OpenCV Farneback optical-flow parameters"
    )
    for line in entries:
        if not line.strip().startswith("#") and line != "    flow_params:":
            assert "  # " in line, line
    shown = yaml.safe_load("\n".join(lines))["projection"]
    assert shown == ProjectionOptions().model_dump()
    assert list(shown) == list(ProjectionOptions.model_fields)


def test_projection_shows_the_meaning_and_the_choices_of_an_option(show):
    lines = show("projection").splitlines()

    assert (
        "    max_projection_steps: 3  "
        "# Scan-step projections computed beyond the registration frame"
    ) in lines
    assert "      winsize: 10  # Averaging window size (pixels)" in lines
    method = next(line for line in lines if line.strip().startswith("method:"))
    assert method.endswith("exists: adapt_default")


def test_tracking_shows_every_option_under_its_own_name_with_its_comment(show):
    lines = show("tracking").splitlines()

    assert "  tracking:" in lines
    assert "  tracker:" not in lines
    entries = lines[lines.index("  tracking:") + 1 :]
    for line in entries:
        assert "  # " in line, line
    shown = yaml.safe_load("\n".join(lines))["tracking"]
    assert shown == TrackingOptions().model_dump()
    assert list(shown) == list(TrackingOptions.model_fields)


def test_tracking_shows_the_meaning_and_the_choices_of_an_option(show):
    lines = show("tracking").splitlines()

    assert "    max_speed_ms: 40.0  # Hard speed cap v_max (m/s) of any link" in lines
    assert "    uid_width: 10  # Characters in each base-36 cell_uid" in lines
    polarity = next(line for line in lines if line.strip().startswith("polarity:"))
    assert polarity.endswith("(brightness temperature): 1 | -1")


def test_analysis_shows_every_option_under_its_own_name_with_its_comment(show):
    lines = show("analysis").splitlines()

    assert "  analysis:" in lines
    assert "  analyzer:" not in lines
    entries = lines[lines.index("  analysis:") + 1 :]
    for line in entries:
        assert "  # " in line, line
    shown = yaml.safe_load("\n".join(lines))["analysis"]
    assert shown == AnalysisOptions().model_dump(mode="json")
    assert list(shown) == list(AnalysisOptions.model_fields)


def test_analysis_shows_the_meaning_of_each_option(show):
    lines = show("analysis").splitlines()

    assert (
        "    radar_variables: [reflectivity, velocity, differential_phase, "
        "differential_reflectivity, spectrum_width, cross_correlation_ratio]  "
        "# Variables that get per-cell statistics, when present in the scan"
    ) in lines
    assert (
        "    adjacency_min_touching_boundary_pixels: 1  # Min number of touching boundary "
        "pixels to count two labels as adjacent in the same scan"
    ) in lines
    excluded = next(line for line in lines if line.strip().startswith("exclude_fields:"))
    assert excluded.startswith(
        "    exclude_fields: []  # Variables never analysed, even when in radar_variables; "
        "entries you give are added to "
    )
    assert excluded.endswith(f"({', '.join(ALWAYS_EXCLUDED)})")


def test_options_owned_by_the_run_section_are_not_offered(show):
    downloader = yaml.safe_load(show("downloader"))["downloader"]

    assert not {"radar", "mode", "start_time", "end_time"} & set(downloader)


def test_global_offers_only_the_values_the_modules_share(show):
    shared = yaml.safe_load(show("global"))["global"]

    assert set(shared) == {"z_level", "max_scan_gap_minutes", "tracking_field"}


def test_global_shows_each_option_with_its_default_and_its_meaning(show):
    assert show("global").splitlines()[1:] == [
        "  global:",
        "    z_level: 2000.0  # Analysis altitude in meters",
        "    max_scan_gap_minutes: 10.0  # Largest gap (minutes) between two scans that any "
        "stage pairs across; beyond it no seed is carried, no projection is computed and "
        "tracking resets",
        "    tracking_field: reflectivity  # Canonical field that drives detection, "
        "projection, and tracking",
    ]


def test_downloader_shows_each_option_with_its_default_and_its_meaning(show):
    assert show("downloader").splitlines()[1:] == [
        "  downloader:",
        "    latest_files: 5  # Realtime: newest scans taken from each poll's latest_minutes "
        "window",
        "    latest_minutes: 60  # Realtime: how far back (minutes) each poll looks for scans",
        "    poll_interval_sec: 300  # Polling interval in seconds",
        "    max_fetch_retries: 3  # AWS scan-fetch attempts before giving up for this poll",
        "    min_file_size: 1024  # Minimum file size in bytes to consider valid",
        "    max_queue_size: 100  # Scans the downloader may run ahead of the processor; at "
        "this many queued scans the downloader pauses, while processing never pauses",
        "    queue_resume_fraction: 0.1  # The downloader resumes once the queue has drained "
        "to this fraction of max_queue_size (at least 1 scan)",
    ]


def test_logging_shows_each_option_with_its_default_and_its_meaning(show):
    levels = "DEBUG | INFO | WARNING | ERROR | CRITICAL"

    assert show("logging").splitlines()[1:] == [
        "  logging:",
        "    level: INFO  # Level of the run's log file (logs/pipeline_<radar>.log); the "
        f"console has its own console_level: {levels}",
        f"    console_level: WARNING  # Level of the log messages printed on the console: {levels}",
        "    console_logs: true  # Print log messages on the console, with a live status line "
        "when it is a terminal",
        "    json_logs: false  # Write the run's log file as JSON lines, not text",
        "    enabled: true  # Collect the run's telemetry; off, traces and metrics are off too",
        "    traces: true  # Record each module's timing span, kept as the run's module history",
        "    metrics: true  # Count files, scans, cells and timings for the end-of-run summary",
    ]


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


def test_target_selection_prints_its_packaged_rule_with_its_comments(show):
    out = show("target_selection")
    preset = TSE_PRESET.read_text("utf-8")

    assert out.splitlines()[0] == PARAMS_LINE
    expected = yaml.safe_load(preset)["target_selection"]
    assert yaml.safe_load(out)["target_selection"] == expected
    rationale = next(line for line in preset.splitlines() if "decided at the 2nd" in line)
    assert f"  {rationale}" in out.splitlines()


def test_arm_radar_comm_prints_the_sandbox_preset_a_dry_run(show):
    section = yaml.safe_load(show("arm_radar_comm"))["arm_radar_comm"]

    assert section == yaml.safe_load(SANDBOX_PRESET.read_text("utf-8"))["arm_radar_comm"]
    assert section["api"] is None


def test_arm_radar_comm_is_printed_as_options_to_give_not_defaults(show):
    lines = show("arm_radar_comm").splitlines()

    assert lines[2] == (
        "  # arm_radar_comm has no defaults: a file that lists it must give every option below."
    )


def test_an_unknown_name_lists_every_valid_name_by_kind(adapt, capsys):
    assert adapt("config", "--params", "bogus") == 2

    err = capsys.readouterr().err
    assert "bogus" in err
    for kind in ("modules", "params sections"):
        assert kind in err
    for name in _names():
        assert name in err


def test_the_sections_shown_are_downloader_global_and_logging(adapt, capsys):
    adapt("config", "--params", "bogus")

    assert "  params sections: downloader, global, logging" in capsys.readouterr().err.splitlines()


@pytest.mark.parametrize(
    "name",
    [
        "visualization",
        "output",
        "segmenter",
        "projector",
        "tracker",
        "analyzer",
        "reader",
        "regridder",
    ],
)
def test_a_section_nothing_reads_is_an_unknown_name(name, adapt, capsys):
    assert adapt("config", "--params", name) == 2

    assert f"Unknown name(s) {name} " in capsys.readouterr().err


@pytest.mark.parametrize("name", _names())
def test_pasting_the_fragment_under_its_line_changes_nothing(
    name, show, adapt, tmp_path, monkeypatch
):
    template = _template(tmp_path, adapt, monkeypatch)
    template = template.replace(f"  # - {name}", f"  - {name}")  # its params need it listed

    pipeline = _load(tmp_path, _paste(template, show(name)))

    if name in RULES:
        RULES[name].model_validate(pipeline.params[name])
        assert pipeline.params[name] == yaml.safe_load(registry.get(name).options_preset())[name]
    else:
        assert _at_their_defaults(pipeline)


def test_two_fragments_pasted_one_after_the_other_under_params_load(
    show, adapt, tmp_path, monkeypatch
):
    template = _template(tmp_path, adapt, monkeypatch)
    # params: is the template's last line, so appending pastes under it (--params NAME >> file).
    pipeline = _load(tmp_path, template + show("detection") + show("tracking"))

    assert set(pipeline.params) == {"detection", "tracking"}
    assert _at_their_defaults(pipeline)


def test_several_modules_print_under_one_line_two_empty_lines_apart(show):
    out = show("tracking", "projection")

    assert out.count(PARAMS_LINE) == 1
    assert out == PARAMS_LINE + "\n" + "\n\n".join(
        show(name).removeprefix(PARAMS_LINE + "\n") for name in ("tracking", "projection")
    )
    assert "\n\n\n  # projection: " in out
    assert list(yaml.safe_load(out)) == ["tracking", "projection"]


def test_a_module_and_a_section_print_together(show):
    assert list(yaml.safe_load(show("global", "tracking"))) == ["global", "tracking"]


def test_every_unknown_name_is_told_at_once(adapt, capsys):
    assert adapt("config", "--params", "tracking", "bogus", "trackin") == 2

    assert "Unknown name(s) bogus, trackin for adapt config --params" in capsys.readouterr().err
