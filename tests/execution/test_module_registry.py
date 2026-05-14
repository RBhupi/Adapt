"""Tests for ModuleRegistry.

Unit tests only — no IO, no radar data.
All modules are lightweight stubs inheriting BaseModule.
"""

import pytest

from adapt.execution.module_registry import ModuleRegistry
from adapt.modules.base import BaseModule

# ---------------------------------------------------------------------------
# Stub modules for testing
# ---------------------------------------------------------------------------


class StubA(BaseModule):
    name = "stub_a"
    inputs = []
    outputs = ["a_out"]

    def run(self, context):
        return {"a_out": "a_result"}


class StubB(BaseModule):
    name = "stub_b"
    inputs = ["a_out"]
    outputs = ["b_out"]

    def run(self, context):
        return {"b_out": "b_result"}


class StubC(BaseModule):
    name = "stub_c"
    inputs = ["b_out"]
    outputs = ["c_out"]

    def run(self, context):
        return {"c_out": "c_result"}


class EmptyNameModule(BaseModule):
    name = ""
    inputs = []
    outputs = []

    def run(self, context):
        return {}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def reg():
    """Fresh registry per test — isolated from the global singleton."""
    return ModuleRegistry()


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


class TestRegistration:

    @pytest.mark.unit
    def test_register_single_module(self, reg):
        reg.register(StubA)
        assert "stub_a" in reg

    @pytest.mark.unit
    def test_register_multiple_modules(self, reg):
        reg.register(StubA)
        reg.register(StubB)
        assert "stub_a" in reg
        assert "stub_b" in reg

    @pytest.mark.unit
    def test_list_modules_returns_names(self, reg):
        reg.register(StubA)
        reg.register(StubB)
        names = reg.list_modules()
        assert "stub_a" in names
        assert "stub_b" in names
        assert len(names) == 2

    @pytest.mark.unit
    def test_len_reflects_registered_count(self, reg):
        assert len(reg) == 0
        reg.register(StubA)
        assert len(reg) == 1
        reg.register(StubB)
        assert len(reg) == 2

    @pytest.mark.unit
    def test_register_empty_name_raises(self, reg):
        with pytest.raises(ValueError, match="empty name"):
            reg.register(EmptyNameModule)

    @pytest.mark.unit
    def test_duplicate_registration_raises(self, reg):
        reg.register(StubA)

        class StubADuplicate(BaseModule):
            name = "stub_a"  # same name as StubA
            inputs = []
            outputs = ["different_out"]

            def run(self, context):
                return {}

        with pytest.raises(RuntimeError, match="stub_a"):
            reg.register(StubADuplicate)

    @pytest.mark.unit
    def test_unregister_removes_module(self, reg):
        reg.register(StubA)
        reg.unregister("stub_a")
        assert "stub_a" not in reg

    @pytest.mark.unit
    def test_unregister_nonexistent_is_noop(self, reg):
        reg.unregister("does_not_exist")  # should not raise

    @pytest.mark.unit
    def test_clear_removes_all(self, reg):
        reg.register(StubA)
        reg.register(StubB)
        reg.clear()
        assert len(reg) == 0


# ---------------------------------------------------------------------------
# Retrieval tests
# ---------------------------------------------------------------------------


class TestRetrieval:

    @pytest.mark.unit
    def test_get_returns_class(self, reg):
        reg.register(StubA)
        cls = reg.get("stub_a")
        assert cls is StubA

    @pytest.mark.unit
    def test_get_unknown_raises(self, reg):
        with pytest.raises(KeyError, match="not registered"):
            reg.get("unknown")

    @pytest.mark.unit
    def test_contains_true_after_register(self, reg):
        reg.register(StubA)
        assert "stub_a" in reg

    @pytest.mark.unit
    def test_contains_false_before_register(self, reg):
        assert "stub_a" not in reg


# ---------------------------------------------------------------------------
# create_modules tests
# ---------------------------------------------------------------------------


class TestCreateModules:

    @pytest.mark.unit
    def test_create_modules_returns_instances(self, reg):
        reg.register(StubA)
        modules = reg.create_modules()
        assert len(modules) == 1
        assert isinstance(modules[0], StubA)

    @pytest.mark.unit
    def test_create_modules_returns_fresh_instances(self, reg):
        reg.register(StubA)
        m1 = reg.create_modules()[0]
        m2 = reg.create_modules()[0]
        assert m1 is not m2  # different instances

    @pytest.mark.unit
    def test_create_modules_preserves_order(self, reg):
        reg.register(StubA)
        reg.register(StubB)
        reg.register(StubC)
        modules = reg.create_modules()
        names = [m.name for m in modules]
        assert names == ["stub_a", "stub_b", "stub_c"]

    @pytest.mark.unit
    def test_create_modules_empty_registry(self, reg):
        assert reg.create_modules() == []

    @pytest.mark.unit
    def test_created_instances_are_runnable(self, reg):
        reg.register(StubA)
        module = reg.create_modules()[0]
        result = module.run({})
        assert result == {"a_out": "a_result"}


# ---------------------------------------------------------------------------
# Integration: registry → graph builder
# ---------------------------------------------------------------------------


class TestRegistryGraphIntegration:

    @pytest.mark.unit
    def test_create_modules_feeds_graph_builder(self, reg):
        """Modules from registry can be used directly with GraphBuilder."""
        from adapt.execution.graph.builder import GraphBuilder
        from adapt.execution.graph.executor import GraphExecutor

        reg.register(StubA)
        reg.register(StubB)

        modules = reg.create_modules()
        nodes = GraphBuilder(modules).build()
        ctx = GraphExecutor(nodes).run({})

        assert ctx["a_out"] == "a_result"
        assert ctx["b_out"] == "b_result"

    @pytest.mark.unit
    def test_full_linear_pipeline_via_registry(self, reg):
        """Three-module chain registers, builds, and executes correctly."""
        from adapt.execution.graph.builder import GraphBuilder
        from adapt.execution.graph.executor import GraphExecutor

        reg.register(StubA)
        reg.register(StubB)
        reg.register(StubC)

        modules = reg.create_modules()
        nodes = GraphBuilder(modules).build()
        ctx = GraphExecutor(nodes).run({})

        assert ctx["c_out"] == "c_result"
