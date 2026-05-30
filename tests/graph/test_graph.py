"""Tests for the execution graph: Node, GraphBuilder, GraphExecutor.

These are pure unit tests — no IO, no radar data, no dependencies on
scientific modules. All modules are lightweight stubs.
"""

import pytest

from adapt.contracts import ContractViolation
from adapt.execution.graph.builder import GraphBuilder
from adapt.execution.graph.executor import GraphExecutor
from adapt.execution.graph.node import Node
from adapt.modules.base import BaseModule

# ---------------------------------------------------------------------------
# Stub modules for testing
# ---------------------------------------------------------------------------


class StubModule(BaseModule):
    """A configurable stub module that records execution order."""

    def __init__(self, name, inputs, outputs, side_effect=None):
        self._name = name
        self._inputs = inputs
        self._outputs = outputs
        self._side_effect = side_effect  # callable(context) -> dict

    @property
    def name(self):
        return self._name

    @property
    def inputs(self):
        return self._inputs

    @property
    def outputs(self):
        return self._outputs

    def run(self, context):
        if self._side_effect:
            return self._side_effect(context)
        return {k: f"{self._name}_result" for k in self._outputs}


# Execution order tracker shared across test stubs
_execution_order = []


def _make_tracking_module(name, inputs, outputs):
    """Create a stub that appends its name to _execution_order when run."""

    def run_fn(ctx):
        _execution_order.append(name)
        return {k: f"{name}_result" for k in outputs}

    return StubModule(name, inputs, outputs, side_effect=run_fn)


# ---------------------------------------------------------------------------
# Node tests
# ---------------------------------------------------------------------------


class TestNode:
    @pytest.mark.unit
    def test_node_wraps_module(self):
        mod = StubModule("grid", ["radar_volume"], ["grid_volume"])
        node = Node(mod)
        assert node.name == "grid"
        assert node.inputs == ["radar_volume"]
        assert node.outputs == ["grid_volume"]

    @pytest.mark.unit
    def test_node_starts_with_empty_deps(self):
        mod = StubModule("grid", ["radar_volume"], ["grid_volume"])
        node = Node(mod)
        assert node.dependencies == []
        assert node.dependents == []

    @pytest.mark.unit
    def test_node_repr(self):
        mod = StubModule("detect", ["grid_volume"], ["storm_cells"])
        node = Node(mod)
        r = repr(node)
        assert "detect" in r
        assert "grid_volume" in r
        assert "storm_cells" in r


# ---------------------------------------------------------------------------
# GraphBuilder tests
# ---------------------------------------------------------------------------


class TestGraphBuilder:
    @pytest.mark.unit
    def test_build_linear_chain(self):
        """A → B → C should produce correct dependencies."""
        a = StubModule("a", [], ["x"])
        b = StubModule("b", ["x"], ["y"])
        c = StubModule("c", ["y"], ["z"])

        nodes = {n.name: n for n in GraphBuilder([a, b, c]).build()}

        assert nodes["b"].dependencies == [nodes["a"]]
        assert nodes["c"].dependencies == [nodes["b"]]
        assert nodes["a"].dependencies == []

    @pytest.mark.unit
    def test_build_dependents_wired(self):
        """Dependents should mirror dependencies."""
        a = StubModule("a", [], ["x"])
        b = StubModule("b", ["x"], ["y"])

        nodes = {n.name: n for n in GraphBuilder([a, b]).build()}

        assert nodes["b"] in nodes["a"].dependents

    @pytest.mark.unit
    def test_build_fan_out(self):
        """One output feeding two modules."""
        source = StubModule("source", [], ["data"])
        consumer1 = StubModule("consumer1", ["data"], ["out1"])
        consumer2 = StubModule("consumer2", ["data"], ["out2"])

        nodes = {n.name: n for n in GraphBuilder([source, consumer1, consumer2]).build()}

        assert nodes["source"] in nodes["consumer1"].dependencies
        assert nodes["source"] in nodes["consumer2"].dependencies
        assert len(nodes["source"].dependents) == 2

    @pytest.mark.unit
    def test_build_root_node_no_deps(self):
        """A module with no declared inputs has no dependencies."""
        root = StubModule("root", [], ["data"])
        nodes = GraphBuilder([root]).build()
        assert nodes[0].dependencies == []

    @pytest.mark.unit
    def test_build_unconnected_inputs_ignored(self):
        """Inputs that no module produces are simply ignored (external data)."""
        mod = StubModule("mod", ["external_input"], ["result"])
        nodes = GraphBuilder([mod]).build()
        assert nodes[0].dependencies == []

    @pytest.mark.unit
    def test_build_duplicate_output_raises(self):
        """Two modules declaring the same output key should raise ValueError."""
        a = StubModule("a", [], ["shared_key"])
        b = StubModule("b", [], ["shared_key"])
        with pytest.raises(ValueError, match="shared_key"):
            GraphBuilder([a, b]).build()

    @pytest.mark.unit
    def test_build_returns_all_nodes(self):
        mods = [
            StubModule("a", [], ["x"]),
            StubModule("b", ["x"], ["y"]),
            StubModule("c", ["y"], ["z"]),
        ]
        nodes = GraphBuilder(mods).build()
        assert len(nodes) == 3


# ---------------------------------------------------------------------------
# GraphExecutor tests
# ---------------------------------------------------------------------------


class TestGraphExecutor:
    def setup_method(self):
        """Clear shared tracker before each test."""
        _execution_order.clear()

    @pytest.mark.unit
    def test_executor_linear_order(self):
        """Nodes must execute in topological order."""
        a = _make_tracking_module("a", [], ["x"])
        b = _make_tracking_module("b", ["x"], ["y"])
        c = _make_tracking_module("c", ["y"], ["z"])

        nodes = GraphBuilder([a, b, c]).build()
        GraphExecutor(nodes).run({})

        assert _execution_order == ["a", "b", "c"]

    @pytest.mark.unit
    def test_executor_root_before_dependents(self):
        """Root nodes (no deps) must execute before their dependents."""
        root = _make_tracking_module("root", [], ["data"])
        child = _make_tracking_module("child", ["data"], ["out"])

        nodes = GraphBuilder([root, child]).build()
        GraphExecutor(nodes).run({})

        assert _execution_order.index("root") < _execution_order.index("child")

    @pytest.mark.unit
    def test_executor_fan_out_both_consumers_run(self):
        """Both consumers should run when source produces their shared input."""
        source = StubModule("source", [], ["data"])
        c1 = StubModule("c1", ["data"], ["out1"])
        c2 = StubModule("c2", ["data"], ["out2"])

        nodes = GraphBuilder([source, c1, c2]).build()
        result = GraphExecutor(nodes).run({})

        assert "out1" in result
        assert "out2" in result

    @pytest.mark.unit
    def test_executor_outputs_merged_into_context(self):
        """Module outputs should appear in the returned context."""
        mod = StubModule("mod", [], ["result"], side_effect=lambda ctx: {"result": 42})
        nodes = GraphBuilder([mod]).build()
        ctx = GraphExecutor(nodes).run({})
        assert ctx["result"] == 42

    @pytest.mark.unit
    def test_executor_initial_context_available(self):
        """Modules should see initial context values."""
        received = {}

        def capture(ctx):
            received.update(ctx)
            return {"out": True}

        mod = StubModule("mod", [], ["out"], side_effect=capture)
        nodes = GraphBuilder([mod]).build()
        GraphExecutor(nodes).run({"initial_key": "hello"})

        assert received.get("initial_key") == "hello"

    @pytest.mark.unit
    def test_executor_cycle_raises(self):
        """A cyclic graph must raise RuntimeError, not hang."""
        # Create two nodes that depend on each other by manually wiring
        a = StubModule("a", ["b_out"], ["a_out"])
        b = StubModule("b", ["a_out"], ["b_out"])

        # Manually build nodes with circular deps (bypasses GraphBuilder)
        node_a = Node(a)
        node_b = Node(b)
        node_a.dependencies.append(node_b)
        node_b.dependencies.append(node_a)

        with pytest.raises(RuntimeError, match="cycle"):
            GraphExecutor([node_a, node_b]).run({})

    @pytest.mark.unit
    def test_executor_single_node(self):
        """Single node with no deps runs and returns output."""
        mod = StubModule("solo", [], ["result"], side_effect=lambda ctx: {"result": "done"})
        nodes = GraphBuilder([mod]).build()
        ctx = GraphExecutor(nodes).run({})
        assert ctx["result"] == "done"


# ---------------------------------------------------------------------------
# GraphExecutor contract enforcement tests
# ---------------------------------------------------------------------------


class _ContractStub(BaseModule):
    """Minimal BaseModule subclass with configurable contracts for testing."""

    def __init__(
        self,
        name,
        inputs,
        outputs,
        run_fn=None,
        input_contracts=None,
        output_contracts=None,
    ):
        self._name = name
        self._inputs = inputs
        self._outputs = outputs
        self._run_fn = run_fn or (lambda ctx: {k: f"{name}_out" for k in outputs})
        self.input_contracts = input_contracts or {}
        self.output_contracts = output_contracts or {}

    @property
    def name(self):
        return self._name

    @property
    def inputs(self):
        return self._inputs

    @property
    def outputs(self):
        return self._outputs

    def run(self, context):
        return self._run_fn(context)


class TestGraphExecutorContracts:
    @pytest.mark.unit
    def test_input_contract_is_called_before_run(self):
        """Input validator must be called before module.run()."""
        call_order = []

        def _validate(val):
            call_order.append("validate")

        def _run(ctx):
            call_order.append("run")
            return {"out": 1}

        mod = _ContractStub("m", ["inp"], ["out"], run_fn=_run, input_contracts={"inp": _validate})
        nodes = GraphBuilder([mod]).build()
        GraphExecutor(nodes).run({"inp": "value"})

        assert call_order == ["validate", "run"]

    @pytest.mark.unit
    def test_output_contract_is_called_after_run(self):
        """Output validator must be called after module.run()."""
        call_order = []

        def _run(ctx):
            call_order.append("run")
            return {"out": 42}

        def _validate(val):
            call_order.append("validate")

        mod = _ContractStub("m", [], ["out"], run_fn=_run, output_contracts={"out": _validate})
        nodes = GraphBuilder([mod]).build()
        GraphExecutor(nodes).run({})

        assert call_order == ["run", "validate"]

    @pytest.mark.unit
    def test_input_contract_violation_propagates(self):
        """ContractViolation raised by input validator propagates out of executor."""

        def _bad_validator(val):
            raise ContractViolation("input contract broken")

        mod = _ContractStub("m", ["inp"], ["out"], input_contracts={"inp": _bad_validator})
        nodes = GraphBuilder([mod]).build()

        with pytest.raises(ContractViolation, match="input contract broken"):
            GraphExecutor(nodes).run({"inp": "anything"})

    @pytest.mark.unit
    def test_output_contract_violation_propagates(self):
        """ContractViolation raised by output validator propagates out of executor."""

        def _bad_validator(val):
            raise ContractViolation("output contract broken")

        mod = _ContractStub("m", [], ["out"], output_contracts={"out": _bad_validator})
        nodes = GraphBuilder([mod]).build()

        with pytest.raises(ContractViolation, match="output contract broken"):
            GraphExecutor(nodes).run({})

    @pytest.mark.unit
    def test_missing_input_key_raises_contract_violation(self):
        """Executor must raise ContractViolation when a required input is absent.

        Previously the executor silently skipped validation if the key was missing
        from context (guarded by 'if key in context:'). After the fix, it raises
        immediately with a clear message.
        """

        def _validate(val):
            pass  # should never be called — key is absent

        mod = _ContractStub(
            "m", ["required_key"], ["out"], input_contracts={"required_key": _validate}
        )
        nodes = GraphBuilder([mod]).build()

        with pytest.raises(ContractViolation, match="required_key"):
            GraphExecutor(nodes).run({})  # required_key intentionally absent

    @pytest.mark.unit
    def test_input_contract_receives_correct_value(self):
        """Input validator receives the actual value from context."""
        received = []

        def _capture(val):
            received.append(val)

        mod = _ContractStub("m", ["x"], ["out"], input_contracts={"x": _capture})
        nodes = GraphBuilder([mod]).build()
        GraphExecutor(nodes).run({"x": 99})

        assert received == [99]

    @pytest.mark.unit
    def test_output_contract_receives_correct_value(self):
        """Output validator receives the value the module returned."""
        received = []

        def _capture(val):
            received.append(val)

        mod = _ContractStub(
            "m",
            [],
            ["result"],
            run_fn=lambda ctx: {"result": "hello"},
            output_contracts={"result": _capture},
        )
        nodes = GraphBuilder([mod]).build()
        GraphExecutor(nodes).run({})

        assert received == ["hello"]


# ---------------------------------------------------------------------------
# Branch-coverage gap tests (executor lines 82, 100→106, 102→101 and
# builder branches 71→73, 73→68)
# ---------------------------------------------------------------------------


class TestExecutorBranchGaps:
    """Targeted tests for executor branches not reachable by the main suite."""

    @pytest.mark.unit
    def test_completed_node_skipped_on_second_iteration(self):
        """Executor loop skips already-completed nodes (line 82 branch).

        Modules are given in REVERSE dependency order so B comes before A
        in self.nodes but cannot run until A completes.  On the second while
        iteration A is already in `completed` — the `continue` on line 82 fires.
        """
        order = []
        A = StubModule("a", [], ["x"], side_effect=lambda ctx: order.append("a") or {"x": 1})
        B = StubModule("b", ["x"], ["y"], side_effect=lambda ctx: order.append("b") or {"y": 2})
        # Reverse order: B first, then A
        nodes = GraphBuilder([B, A]).build()
        result = GraphExecutor(nodes).run({})
        assert result["x"] == 1
        assert result["y"] == 2
        assert order == ["a", "b"]

    @pytest.mark.unit
    def test_module_returning_none_is_treated_as_no_output(self):
        """If run() returns None the executor does not update context (branch 100→106)."""
        mod = StubModule("sink", ["x"], [], side_effect=lambda ctx: None)
        nodes = GraphBuilder([mod]).build()
        result = GraphExecutor(nodes).run({"x": 42})
        # x stays in context (unchanged), no crash
        assert result["x"] == 42

    @pytest.mark.unit
    def test_output_contract_key_absent_from_outputs_is_skipped(self):
        """If an output_contract key is not in the returned dict, validator is not called
        (branch 102→101).  Executor must not raise — the missing key is silently skipped.
        """
        validated = []

        def _validator(val):
            validated.append(val)

        # Module declares contract for "missing_key" but only returns "real_key"
        mod = _ContractStub(
            "m",
            [],
            ["real_key"],
            run_fn=lambda ctx: {"real_key": "ok"},
            output_contracts={"missing_key": _validator},
        )
        nodes = GraphBuilder([mod]).build()
        result = GraphExecutor(nodes).run({})
        # Validator never called because key absent
        assert validated == []
        assert result["real_key"] == "ok"


class TestBuilderBranchGaps:
    """Targeted tests for GraphBuilder branches 71→73 and 73→68."""

    @pytest.mark.unit
    def test_single_parent_shared_by_multiple_inputs_wired_once(self):
        """When module B consumes two keys both produced by module A, the
        dependency is added only once (branches 71→73 and 73→68 fire on
        the second shared input).
        """
        A = StubModule("a", [], ["x", "y"])  # produces two keys
        B = StubModule("b", ["x", "y"], ["z"])  # consumes both
        nodes = GraphBuilder([A, B]).build()
        node_map = {n.name: n for n in nodes}

        # A is a dependency of B exactly once, not twice
        assert node_map["b"].dependencies.count(node_map["a"]) == 1
        # B is a dependent of A exactly once
        assert node_map["a"].dependents.count(node_map["b"]) == 1
