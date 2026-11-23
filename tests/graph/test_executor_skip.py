"""Which nodes the executor skips, which it lets fail, and what it refuses.

A node is skipped when it needs more prior scans than are usable, and so is
every node downstream of a skipped or failed one. A node that raises has failed:
the rest of the pass goes on. A producer that ran without returning a key a
later node consumes is a bug and raises: it must never look like a skip.
"""

import random
from datetime import UTC, datetime

import pytest

from adapt.contracts import ContractViolation
from adapt.execution.graph.builder import GraphBuilder
from adapt.execution.graph.executor import GraphExecutor
from adapt.modules.base import BaseModule
from adapt.runtime.observability import ObsSettings, build_observability

pytestmark = pytest.mark.unit


def _module(name, inputs, outputs, *, required_history=1, returns=None) -> BaseModule:
    produced = {key: name for key in outputs} if returns is None else returns
    cls = type(
        f"_{name}",
        (BaseModule,),
        {
            "name": name,
            "inputs": inputs,
            "outputs": outputs,
            "required_history": required_history,
            "run": lambda self, ctx: dict(produced),
        },
    )
    return cls()


def _chain():
    """detection (h=1) -> projection (h=2) -> tracking (h=1)."""
    return [
        _module("detection", [], ["segmented_ds"]),
        _module("projection", ["segmented_ds"], ["projected_ds"], required_history=2),
        _module("tracking", ["projected_ds"], ["tracked_cells"]),
    ]


def test_a_node_short_of_history_and_its_dependents_are_skipped():
    nodes = GraphBuilder(_chain(), provided=frozenset()).build()

    run = GraphExecutor(nodes).run({}, history=0)

    assert set(run.skipped) == {"projection", "tracking"}
    assert run.skipped["projection"] == "needs 1 prior scan(s), 0 usable"
    assert run.skipped["tracking"] == "needs 'projection', which was skipped"
    assert set(run.context) == {"segmented_ds"}


def test_every_node_runs_once_enough_prior_scans_are_usable():
    nodes = GraphBuilder(_chain(), provided=frozenset()).build()

    run = GraphExecutor(nodes).run({}, history=1)

    assert run.skipped == {} and run.failed == {}
    assert set(run.context) == {"segmented_ds", "projected_ds", "tracked_cells"}


def _failing(name, inputs, outputs) -> BaseModule:
    def run(self, ctx):
        raise ValueError("kaboom")

    return type(
        f"_{name}", (BaseModule,), {"name": name, "inputs": inputs, "outputs": outputs, "run": run}
    )()


def test_a_node_that_raises_fails_and_its_dependents_are_skipped():
    modules = [
        _module("detection", [], ["segmented_ds"]),
        _failing("projection", ["segmented_ds"], ["projected_ds"]),
        _module("tracking", ["projected_ds"], ["tracked_cells"]),
    ]
    nodes = GraphBuilder(modules, provided=frozenset()).build()

    run = GraphExecutor(nodes).run({}, history=1)

    assert str(run.failed["projection"]) == "kaboom"
    assert run.skipped == {"tracking": "needs 'projection', which failed"}
    assert set(run.context) == {"segmented_ds"}


def test_nodes_that_do_not_need_a_failed_one_still_run():
    modules = [
        _module("detection", [], ["segmented_ds"]),
        _failing("tracking", ["segmented_ds"], ["tracked_cells"]),
        _module("volume_stats", ["segmented_ds"], ["volume_rows"]),
    ]
    nodes = GraphBuilder(modules, provided=frozenset()).build()

    run = GraphExecutor(nodes).run({}, history=0)

    assert set(run.failed) == {"tracking"}
    assert run.context["volume_rows"] == "volume_stats"


def test_after_node_sees_each_node_that_ran_with_its_outputs():
    seen = []
    nodes = GraphBuilder(_chain(), provided=frozenset()).build()

    GraphExecutor(nodes).run(
        {}, history=0, after_node=lambda node, context: seen.append((node.name, set(context)))
    )

    assert seen == [("detection", {"segmented_ds"})]


def test_a_node_whose_outputs_cannot_be_handed_on_has_failed():
    def refuse(node, context):
        if node.name == "projection":
            raise OSError("disk full")

    nodes = GraphBuilder(_chain(), provided=frozenset()).build()

    run = GraphExecutor(nodes).run({}, history=1, after_node=refuse)

    assert isinstance(run.failed["projection"], OSError)
    assert set(run.skipped) == {"tracking"}


def test_a_contract_violation_ends_the_pass():
    def violate(self, ctx):
        raise ContractViolation("bad grid")

    module = type("_ingest", (BaseModule,), {"name": "ingest", "outputs": ["g"], "run": violate})()
    nodes = GraphBuilder([module], provided=frozenset()).build()

    with pytest.raises(ContractViolation, match="bad grid"):
        GraphExecutor(nodes).run({}, history=0)


def test_a_producer_that_ran_without_its_consumed_key_raises():
    modules = [
        _module("detection", [], ["segmented_ds"], returns={}),
        _module("projection", ["segmented_ds"], ["projected_ds"]),
    ]
    nodes = GraphBuilder(modules, provided=frozenset()).build()

    with pytest.raises(ContractViolation) as excinfo:
        GraphExecutor(nodes).run({}, history=0)

    message = str(excinfo.value)
    assert "detection" in message
    assert "segmented_ds" in message


def test_a_module_that_ran_without_every_declared_output_raises_naming_them():
    modules = [
        _module(
            "analysis",
            [],
            ["cell_stats", "cell_adjacency", "scan_cell_count"],
            returns={"cell_stats": "rows"},
        )
    ]
    nodes = GraphBuilder(modules, provided=frozenset()).build()

    with pytest.raises(ContractViolation) as excinfo:
        GraphExecutor(nodes).run({}, history=0)

    assert str(excinfo.value) == (
        "Module 'analysis' ran but did not return its declared output(s) "
        "cell_adjacency, scan_cell_count"
    )


def test_skipped_nodes_open_no_span():
    obs = build_observability(
        ObsSettings(),
        clock=lambda: 0.0,
        wall_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        rng=random.Random(0),
    )
    nodes = GraphBuilder(_chain(), provided=frozenset()).build()

    GraphExecutor(nodes, observability=obs).run({}, history=0)

    assert [span.name for span in obs.drain_spans()] == ["detection"]
