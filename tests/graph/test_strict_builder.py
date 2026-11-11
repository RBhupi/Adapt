"""The builder refuses unbound inputs and reports the graph's data edges.

Every module input must be produced by another module of the graph or named in
``provided`` (what the runtime seeds). ``data_edges`` is the one description of
the graph's wiring, shared by the runtime and ``adapt graph``.
"""

import pytest

from adapt.contracts import SCAN_SEEDS
from adapt.execution.graph.builder import GraphBuilder, data_edges
from adapt.modules.base import BaseModule

pytestmark = pytest.mark.unit


def _module(name: str, inputs: list[str], outputs: list[str]) -> BaseModule:
    cls = type(
        f"_{name}",
        (BaseModule,),
        {"name": name, "inputs": inputs, "outputs": outputs, "run": lambda self, ctx: {}},
    )
    return cls()


def test_an_input_nobody_produces_or_provides_is_refused_by_name():
    projection = _module("projection", ["segmented_ds"], ["projected_ds"])

    with pytest.raises(ValueError) as excinfo:
        GraphBuilder([projection], provided=frozenset({"scan_time"})).build()

    message = str(excinfo.value)
    assert "Module 'projection' needs input 'segmented_ds'" in message
    assert "scan_time" in message  # says what the runtime does provide


def test_every_unbound_input_is_named_in_one_error():
    a = _module("a", ["missing_one"], ["x"])
    b = _module("b", ["x", "missing_two"], ["y"])

    with pytest.raises(ValueError) as excinfo:
        GraphBuilder([a, b], provided=frozenset()).build()

    assert "'a' needs input 'missing_one'" in str(excinfo.value)
    assert "'b' needs input 'missing_two'" in str(excinfo.value)


def test_a_provided_input_builds_without_a_dependency():
    ingest = _module("ingest", ["nexrad_file"], ["grid_ds"])

    (node,) = GraphBuilder([ingest], provided=frozenset({"nexrad_file"})).build()

    assert node.dependencies == []


def test_data_edges_label_each_matched_input_of_a_chain():
    ingest = _module("ingest", ["nexrad_file"], ["grid_ds", "grid_ds_2d"])
    detection = _module("detection", ["grid_ds_2d", "grid_ds"], ["segmented_ds"])
    projection = _module("projection", ["segmented_ds"], ["projected_ds"])

    assert data_edges([ingest, detection, projection]) == [
        ("ingest", "detection", "grid_ds_2d"),
        ("ingest", "detection", "grid_ds"),
        ("detection", "projection", "segmented_ds"),
    ]


def test_data_edges_refuse_two_producers_of_one_key():
    a = _module("a", [], ["shared_key"])
    b = _module("b", [], ["shared_key"])

    with pytest.raises(ValueError, match="shared_key"):
        data_edges([a, b])


def test_scan_seeds_are_the_keys_the_processor_provides():
    assert {"nexrad_file", "run_id", "scan_id", "scan_time", "prior_scans"} == SCAN_SEEDS


def test_a_key_a_module_both_reads_and_writes_is_its_own_state_not_an_edge():
    """The caller hands it back each scan, so it must be provided, like a seed."""
    stateful = _module("tracking", ["segmented_ds", "tracking_state"], ["tracks", "tracking_state"])
    modules = [_module("detection", [], ["segmented_ds"]), stateful]

    assert data_edges(modules) == [("detection", "tracking", "segmented_ds")]
    with pytest.raises(ValueError, match="'tracking' needs input 'tracking_state'"):
        GraphBuilder(modules, provided=frozenset()).build()
    (_, node) = GraphBuilder(modules, provided=frozenset({"tracking_state"})).build()
    assert [dep.name for dep in node.dependencies] == ["detection"]
