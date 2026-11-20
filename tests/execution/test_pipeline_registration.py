# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The module catalog: which classes a pipeline file may list.

``configuration/defaults.yaml`` holds one line per module, naming its class as
``package.file:Class``. Loading the catalog registers each class. It fails
loudly when the list is unreadable or empty, or a line names no module class
(no fallbacks), and may be repeated, because several components load it during
startup.
"""

import pytest
import yaml

from adapt.execution import pipeline_builder
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import (
    _DEFAULTS_YAML,
    _ensure_modules_registered,
    catalog_module_names,
)
from adapt.modules.base import BaseModule

pytestmark = pytest.mark.unit


def _catalog_lines() -> list[str]:
    with open(_DEFAULTS_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)["pipeline"]["modules"]


def _catalog(monkeypatch, tmp_path, *lines: str) -> None:
    """Make ``lines`` the catalog for this test."""
    path = tmp_path / "defaults.yaml"
    path.write_text(yaml.safe_dump({"pipeline": {"modules": list(lines)}}), encoding="utf-8")
    monkeypatch.setattr(pipeline_builder, "_DEFAULTS_YAML", path)


def test_every_line_names_its_class_as_file_and_class():
    for line in _catalog_lines():
        path, _, class_name = line.partition(":")
        assert path and class_name, f"catalog line {line!r} is not package.file:Class"


def test_the_class_each_line_names_is_registered_under_its_name():
    names = catalog_module_names()

    for line, name in zip(_catalog_lines(), names, strict=True):
        cls = registry.get(name)
        assert issubclass(cls, BaseModule)
        assert f"{cls.__module__}:{cls.__name__}" == line


def test_the_catalog_keeps_the_order_target_selection_relies_on():
    """Modules that do not read from each other run in the catalog's order.
    ``target_selection`` reads this scan's cells from the store, volume columns
    included, so ``cell_volume_stats`` must have stored them before it runs:
    an order no declared input gives."""
    names = catalog_module_names()

    assert names.index("cell_volume_stats") < names.index("target_selection")


def test_loading_the_catalog_again_changes_nothing():
    _ensure_modules_registered()
    before = [(name, registry.get(name)) for name in registry.list_modules()]

    _ensure_modules_registered()

    assert [(name, registry.get(name)) for name in registry.list_modules()] == before


def test_unreadable_defaults_yaml_raises(monkeypatch, tmp_path):
    """A missing module list aborts startup — there is no fallback pipeline."""
    monkeypatch.setattr(pipeline_builder, "_DEFAULTS_YAML", tmp_path / "missing.yaml")

    with pytest.raises(RuntimeError, match="Cannot read pipeline module list"):
        _ensure_modules_registered()


def test_empty_module_list_raises(monkeypatch, tmp_path):
    _catalog(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="No pipeline modules declared"):
        _ensure_modules_registered()


@pytest.mark.parametrize("line", ["adapt.execution.graph", ":LoadModule", "adapt.modules.base:"])
def test_a_line_that_is_not_file_and_class_is_refused(monkeypatch, tmp_path, line):
    _catalog(monkeypatch, tmp_path, line)

    with pytest.raises(RuntimeError) as refusal:
        catalog_module_names()

    assert repr(line) in str(refusal.value)
    assert "package.file:Class" in str(refusal.value)


def test_a_line_that_is_not_text_is_refused(monkeypatch, tmp_path):
    """``- package.file: Class`` (a space after the colon) is a mapping to YAML."""
    path = tmp_path / "defaults.yaml"
    path.write_text("pipeline:\n  modules:\n    - adapt.modules.ingest.node: LoadModule\n")
    monkeypatch.setattr(pipeline_builder, "_DEFAULTS_YAML", path)

    with pytest.raises(RuntimeError) as refusal:
        catalog_module_names()

    assert "adapt.modules.ingest.node" in str(refusal.value)
    assert "package.file:Class" in str(refusal.value)


def test_a_class_without_a_module_name_is_refused_naming_its_line(monkeypatch, tmp_path):
    class _Nameless(BaseModule):
        inputs = ()
        outputs = ()

        def run(self, context):
            return {}

    monkeypatch.setattr(pipeline_builder, "_Nameless", _Nameless, raising=False)
    line = "adapt.execution.pipeline_builder:_Nameless"
    _catalog(monkeypatch, tmp_path, line)

    with pytest.raises(RuntimeError) as refusal:
        catalog_module_names()

    assert repr(line) in str(refusal.value)
    assert "has no name" in str(refusal.value)


def test_a_line_whose_file_cannot_be_imported_is_refused(monkeypatch, tmp_path):
    _catalog(monkeypatch, tmp_path, "adapt.no_such_module:Anything")

    with pytest.raises(ImportError, match="adapt.no_such_module:Anything"):
        _ensure_modules_registered()


@pytest.mark.parametrize(
    "line",
    [
        "adapt.modules.base:NoSuchClass",  # nothing of that name
        "adapt.modules.base:abstractmethod",  # not a class
        "adapt.contracts:ProductView",  # a class, but no module
        "adapt.modules.base:BaseModule",  # the interface itself
    ],
)
def test_a_line_that_names_no_module_class_is_refused(monkeypatch, tmp_path, line):
    _catalog(monkeypatch, tmp_path, line)

    with pytest.raises(RuntimeError) as refusal:
        catalog_module_names()

    assert repr(line) in str(refusal.value)
    assert "names no module class" in str(refusal.value)


def test_two_lines_giving_one_module_name_are_refused(monkeypatch, tmp_path):
    class _Other(BaseModule):
        name = "ingest"
        inputs = ()
        outputs = ()

        def run(self, context):
            return {}

    monkeypatch.setattr(pipeline_builder, "_OtherIngest", _Other, raising=False)
    _catalog(
        monkeypatch,
        tmp_path,
        _catalog_lines()[0],
        "adapt.execution.pipeline_builder:_OtherIngest",
    )

    with pytest.raises(RuntimeError, match="'ingest' is already registered"):
        catalog_module_names()

    assert registry.get("ingest").__name__ == "LoadModule"
