"""The live modules' own declarations form one per-scan graph.

Each real module is replaced by a stub carrying its declarations (name, inputs,
outputs, required_history), so these tests pin what the declarations mean for a
scan without running any science.
"""

import pytest

from adapt.execution.graph.builder import data_edges
from adapt.execution.graph.executor import GraphExecutor
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import (
    _ensure_modules_registered,
    product_views,
    scan_graph,
)
from adapt.modules.base import BaseModule

pytestmark = pytest.mark.unit

LIVE = ["ingest", "detection", "projection", "analysis", "tracking", "cell_volume_stats"]


def _declared_as(module_class: type[BaseModule]) -> BaseModule:
    declared = {
        key: getattr(module_class, key) for key in ("name", "inputs", "outputs", "required_history")
    }
    outputs = dict.fromkeys(module_class.outputs, module_class.name)
    return type(f"_{module_class.name}", (BaseModule,), {**declared, "run": lambda s, c: outputs})()


def _live_modules() -> list[BaseModule]:
    _ensure_modules_registered()
    return [_declared_as(registry.get(name)) for name in LIVE]


def _skipped(history: int) -> set[str]:
    """Names of the modules skipped on a scan with ``history`` usable prior scans."""
    return set(GraphExecutor(scan_graph(_live_modules(), {})).run({}, history=history).skipped)


def test_the_views_of_every_catalog_module_are_listed():
    _ensure_modules_registered()

    assert [view.name for view in product_views()] == ["cells_by_scan"]


def test_first_scan_runs_the_modules_that_need_no_earlier_scan():
    """Volume stats need the scan's cells, not their tracks: they do not wait."""
    assert _skipped(history=0) == {"projection", "analysis", "tracking"}


def test_every_module_runs_once_a_prior_scan_is_usable():
    assert _skipped(history=1) == set()


def test_what_each_module_reads_is_a_graph_edge():
    edges = data_edges(_live_modules())

    assert ("detection", "projection", "segmented_ds") in edges
    assert ("ingest", "cell_volume_stats", "grid_ds") in edges
    assert ("detection", "cell_volume_stats", "segmented_ds") in edges
    assert ("analysis", "tracking", "cell_adjacency") in edges
    assert not [edge for edge in edges if edge[:2] == ("tracking", "cell_volume_stats")]


def test_a_module_config_is_provided_by_the_runtime():
    class _NeedsItsConfig(BaseModule):
        name = "probe"
        inputs = ["probe_config", "scan_time", "prior_scans"]
        outputs = ["probe_out"]

        def run(self, context: dict) -> dict:
            return {}

    (node,) = scan_graph([_NeedsItsConfig()], {})

    assert node.dependencies == []


def test_a_module_listed_without_its_producer_is_told_which_module_to_list():
    modules = [m for m in _live_modules() if m.name in ("ingest", "detection", "tracking")]

    with pytest.raises(ValueError) as excinfo:
        scan_graph(modules, {})

    message = str(excinfo.value)
    assert "Module 'tracking' needs input 'projected_ds'" in message
    assert "which module 'projection' produces — list 'projection' too" in message


def test_over_a_stored_run_a_stored_output_needs_no_producer():
    """A module reads from the store what a module not listed stored before."""
    from adapt.execution.nodes.xlma_stat import XlmaStatModule

    (node,) = scan_graph([XlmaStatModule()], {}, stored_run=True)

    assert node.dependencies == []
    with pytest.raises(ValueError, match="list 'tracking' too"):
        scan_graph([XlmaStatModule()], {})


def test_a_modules_options_decide_what_it_reads():
    class _ReadsLessWhenTold(BaseModule):
        name = "probe"
        inputs = ["probe_config", "scan_time", "grid_ds"]
        outputs = ["probe_out"]

        @classmethod
        def inputs_for(cls, config) -> list[str]:
            return cls.inputs if config["needs_grid"] else ["probe_config", "scan_time"]

        def run(self, context: dict) -> dict:
            return {}

    _ensure_modules_registered()  # so ingest is known as grid_ds's producer
    with pytest.raises(ValueError, match="list 'ingest' too"):
        scan_graph([_ReadsLessWhenTold()], {"probe_config": {"needs_grid": True}})

    (node,) = scan_graph([_ReadsLessWhenTold()], {"probe_config": {"needs_grid": False}})
    assert node.inputs == ["probe_config", "scan_time"]


def _real(*names: str) -> list[BaseModule]:
    """The registered modules themselves, with everything they declare."""
    _ensure_modules_registered()
    return [registry.get(name)() for name in names]


def test_an_earlier_scan_output_must_come_from_a_listed_module():
    """Detection's seed carry reads projection's labels of the scan before."""
    with pytest.raises(ValueError) as excinfo:
        scan_graph(_real("ingest", "detection"), {})

    message = str(excinfo.value)
    assert "Module 'detection' reads output 'projected_ds' of earlier scans" in message
    assert "which module 'projection' produces" in message
    assert "list 'projection' under modules:" in message
    assert "or change detection's options so it does not read it" in message
    assert "(adapt config --show detection)" in message


def test_a_module_may_read_its_own_outputs_of_earlier_scans():
    """Detection reads back its own carry ages and cell count: they need no other module."""
    nodes = scan_graph(_real("ingest", "detection", "projection"), {})

    assert [node.name for node in nodes] == ["ingest", "detection", "projection"]


class _ReadsBackWhenTold(BaseModule):
    """Reads the earlier scans' ``cell_stats`` (analysis's, a stored output) when told to."""

    name = "probe"
    inputs = ["probe_config", "prior_scans"]
    outputs = ["probe_out"]
    prior_inputs = ("cell_stats",)

    @classmethod
    def prior_inputs_for(cls, config) -> tuple[str, ...]:
        return cls.prior_inputs if config["reads_back"] else ()

    def run(self, context: dict) -> dict:
        return {}


def test_a_modules_options_decide_what_it_reads_of_earlier_scans():
    _ensure_modules_registered()  # so analysis is known as cell_stats's producer

    with pytest.raises(ValueError, match="list 'analysis' under modules:"):
        scan_graph([_ReadsBackWhenTold()], {"probe_config": {"reads_back": True}})

    (node,) = scan_graph([_ReadsBackWhenTold()], {"probe_config": {"reads_back": False}})
    assert node.dependencies == []


def test_over_a_stored_run_an_earlier_scan_output_still_needs_its_producer():
    """Earlier scans' outputs are kept by the runtime as it goes, never read from the store."""
    _ensure_modules_registered()

    with pytest.raises(ValueError) as excinfo:
        scan_graph([_ReadsBackWhenTold()], {"probe_config": {"reads_back": True}}, stored_run=True)

    assert "Module 'probe' reads output 'cell_stats' of earlier scans" in str(excinfo.value)


def test_an_earlier_scan_output_no_module_produces_is_refused():
    class _ReadsNothingKnown(BaseModule):
        name = "probe"
        inputs = ["prior_scans"]
        outputs = ["probe_out"]
        prior_inputs = ("no_such_output",)

        def run(self, context: dict) -> dict:
            return {}

    _ensure_modules_registered()

    with pytest.raises(ValueError) as excinfo:
        scan_graph([_ReadsNothingKnown()], {})

    message = str(excinfo.value)
    assert "Module 'probe' reads output 'no_such_output' of earlier scans" in message
    assert "no registered module produces" in message
