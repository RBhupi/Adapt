# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt graph NAME``: the pipeline file's module graph as Mermaid text.

The graph is the one a new run of the file would build: each module reads what
the file's options make it read, and a file a run would refuse is refused. The
folder need not be a store.
"""

import pytest
from tests.helpers.pipeline_files import MODULES, write_pipeline

from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule

pytestmark = pytest.mark.unit

RADAR = [*MODULES, "target_selection", "radar_command"]
# Detection carries seeds between scans along projection's flow; without projection it is off.
NO_CARRY = {"segmenter": {"seed_carry_frames": 0}}


@pytest.fixture
def graph(adapt, tmp_path, monkeypatch, capsys):
    """Write ``nexrad.yaml`` (a realtime run unless given); return (exit code, stdout, stderr)."""

    def _graph(**sections):
        sections.setdefault("run", {"source": "aws_nexrad", "radar": "KTST", "mode": "realtime"})
        write_pipeline(tmp_path, "nexrad", **sections)
        monkeypatch.chdir(tmp_path)
        code = adapt("graph", "nexrad")
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _graph


def _listed(text: str) -> list[str]:
    """The node lines of the modules subgraph."""
    lines = text.splitlines()
    start = lines.index("  subgraph modules")
    end = lines.index("  end", start)
    return [line.strip() for line in lines[start + 1 : end]]


def test_prints_a_mermaid_flowchart(graph):
    code, out, _ = graph()

    assert code == 0
    assert out.splitlines()[0] == "flowchart TB"


def test_an_edge_carries_every_key_one_module_hands_another(graph):
    _, out, _ = graph()

    assert "  ingest -->|grid_ds_2d, grid_ds| detection" in out.splitlines()


def test_what_each_module_reads_is_an_edge(graph):
    lines = graph()[1].splitlines()

    assert "  detection -->|segmented_ds| projection" in lines
    assert "  analysis -->|cell_stats, cell_adjacency| tracking" in lines
    assert "  ingest -->|grid_ds| cell_volume_stats" in lines
    assert "  detection -->|segmented_ds| cell_volume_stats" in lines


def test_modules_are_listed_in_file_order(graph):
    reordered = ["tracking", *MODULES[:-2], "cell_volume_stats"]

    assert _listed(graph(modules=reordered)[1]) == reordered


def test_target_selection_and_radar_command_are_modules_like_any_other(graph):
    lines = graph(modules=RADAR)[1].splitlines()

    assert _listed("\n".join(lines)) == RADAR
    assert "  tracking -->|cell_identity| target_selection" in lines
    assert "  target_selection -->|selection| radar_command" in lines
    assert "  tracking -->|analysis_ds| radar_command" in lines
    assert "  cell_volume_stats -->|cell_volume_stats_rows| radar_command" in lines


def test_an_option_that_spares_a_module_an_input_removes_its_edge(graph):
    """A fixed vertical band: radar_command needs no measured cell base and top."""
    band = {"vertical_band": {"altitude_min_m": 500.0, "altitude_max_m": 15000.0}}
    without_volume_stats = [m for m in RADAR if m != "cell_volume_stats"]

    code, out, _ = graph(modules=without_volume_stats, params={"radar_command": {"extent": band}})

    assert code == 0
    assert "cell_volume_stats" not in out
    assert "  tracking -->|analysis_ds| radar_command" in out.splitlines()


def test_a_wrong_option_of_a_module_is_refused_as_the_run_would(graph):
    code, _, err = graph(modules=RADAR, params={"radar_command": {"radar": {"scan_patern": "x"}}})

    assert code == 2
    assert "scan_patern" in err


def test_what_the_runtime_provides_is_drawn_and_module_configs_are_not(graph):
    lines = graph(modules=RADAR)[1].splitlines()

    assert '  scan(["nexrad_file, run_id, scan_id, scan_time"])' in lines
    assert '  prior_scans(["prior_scans"])' in lines
    assert "  prior_scans --> projection" in lines
    assert "  scan -->|nexrad_file, scan_time| ingest" in lines
    # The run's cells so far reach target selection from the store.
    assert '  store[("store")]' in lines
    assert "  store -->|cells_so_far, tracks_so_far| target_selection" in lines
    assert "_config" not in "\n".join(lines)


def test_a_plain_run_reads_nothing_from_the_store(graph):
    assert "store" not in graph()[1]


def test_over_a_stored_run_unlisted_modules_outputs_come_from_the_store(graph):
    run = {"source": "stored_run", "radar": "KTST", "mode": "historical"}

    code, out, _ = graph(run=run, modules=["target_selection", "radar_command"])

    assert code == 0
    lines = out.splitlines()
    assert _listed(out) == ["target_selection", "radar_command"]
    assert "  store -->|cell_identity, cells_so_far, tracks_so_far| target_selection" in lines
    assert "  store -->|analysis_ds, cell_volume_stats_rows| radar_command" in lines
    assert "  target_selection -->|selection| radar_command" in lines


def test_the_whole_graph_is_printed_in_a_fixed_order(graph):
    _, out, _ = graph(modules=["ingest", "detection", "cell_volume_stats"], params=NO_CARRY)

    assert out == (
        "flowchart TB\n"
        '  scan(["nexrad_file, run_id, scan_id, scan_time"])\n'
        '  prior_scans(["prior_scans"])\n'
        "  subgraph modules\n"
        "    ingest\n"
        "    detection\n"
        "    cell_volume_stats\n"
        "  end\n"
        "  scan -->|nexrad_file, scan_time| ingest\n"
        "  prior_scans --> detection\n"
        "  ingest -->|grid_ds_2d, grid_ds| detection\n"
        "  ingest -->|grid_ds| cell_volume_stats\n"
        "  detection -->|segmented_ds| cell_volume_stats\n"
    )


def test_an_unknown_module_fails(graph):
    code, out, err = graph(modules=[*MODULES, "segmentation"])

    assert code == 2
    assert out == ""
    assert "unknown module 'segmentation'" in err


def test_a_module_listed_without_its_producer_fails_by_name(graph):
    code, _, err = graph(modules=["detection"], params=NO_CARRY)

    assert code == 2
    assert "Module 'detection' needs input 'grid_ds_2d'" in err
    assert "list 'ingest' too" in err
    assert "nexrad.yaml" in err


def test_radar_command_without_target_selection_fails_by_name(graph):
    code, _, err = graph(modules=[*MODULES, "radar_command"])

    assert code == 2
    assert "list 'target_selection' too" in err


def test_a_module_input_nobody_produces_or_provides_fails_as_the_run_would(graph):
    """The command builds the runtime's graph, so its strict check applies here too."""

    class _UnboundInputModule(BaseModule):
        name = "unbound_probe"
        inputs = ["no_such_key"]
        outputs = ["unbound_probe_out"]

        def run(self, context: dict) -> dict:
            return {}

    registry.register(_UnboundInputModule)
    try:
        code, out, err = graph(modules=[*MODULES, "unbound_probe"])
    finally:
        registry.unregister("unbound_probe")

    assert code == 2
    assert out == ""
    assert "Module 'unbound_probe' needs input 'no_such_key'" in err
    assert "the runtime does not provide it" in err


def test_a_missing_pipeline_file_says_how_to_make_it(adapt, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert adapt("graph", "nexrad") == 2
    assert "No pipeline file" in capsys.readouterr().err
