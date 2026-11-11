# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Behavioral tests for module discovery (the module catalog).

configuration/defaults.yaml is the single source of truth for the live
pipeline module list, postprocess_defaults.yaml for the post-process list.
Registration must fail loudly when a list is unreadable, empty, or names a
module that cannot be imported (no fallbacks), and must be idempotent because
several components call it during startup.
"""

import importlib
import inspect
import sys

import pytest
import yaml

from adapt.execution import pipeline_builder
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import (
    _DEFAULTS_YAML,
    _POSTPROCESS_DEFAULTS_YAML,
    _ensure_modules_registered,
    catalog_module_names,
)
from adapt.modules.base import BaseModule

pytestmark = pytest.mark.unit


@pytest.fixture
def isolated_postprocess_catalog():
    """The catalog imports the post-process node xlma_stat; drop it afterwards so
    the global registry stays the live-pipeline set the rest of the suite expects."""
    yield
    registry.unregister("xlma_stat")
    sys.modules.pop("adapt.execution.nodes.xlma_stat", None)


def _declared_module_paths() -> list[str]:
    with open(_DEFAULTS_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)["pipeline"]["modules"]


def test_every_declared_module_is_registered():
    """Each path in defaults.yaml registers its BaseModule subclass by name."""
    _ensure_modules_registered()

    for path in _declared_module_paths():
        mod = importlib.import_module(path)
        module_classes = [
            cls
            for _, cls in inspect.getmembers(mod, inspect.isclass)
            if issubclass(cls, BaseModule) and cls is not BaseModule and cls.__module__ == path
        ]
        assert module_classes, f"{path} defines no BaseModule subclass"
        for cls in module_classes:
            assert cls.name in registry, (
                f"{path} defines '{cls.name}' but it is not in the registry — "
                "import-time registration is broken"
            )


def test_registration_is_idempotent():
    """Repeated startup calls must not duplicate, reorder, or raise."""
    _ensure_modules_registered()
    before = registry.list_modules()

    _ensure_modules_registered()

    assert registry.list_modules() == before


def test_unreadable_defaults_yaml_raises(monkeypatch, tmp_path):
    """A missing module list aborts startup — there is no fallback pipeline."""
    monkeypatch.setattr(pipeline_builder, "_DEFAULTS_YAML", tmp_path / "missing.yaml")

    with pytest.raises(RuntimeError, match="Cannot read pipeline module list"):
        _ensure_modules_registered()


def test_empty_module_list_raises(monkeypatch, tmp_path):
    """A defaults.yaml that declares no modules is a configuration error."""
    empty = tmp_path / "defaults.yaml"
    empty.write_text("pipeline:\n  modules: []\n", encoding="utf-8")
    monkeypatch.setattr(pipeline_builder, "_DEFAULTS_YAML", empty)

    with pytest.raises(RuntimeError, match="No pipeline modules declared"):
        _ensure_modules_registered()


def test_unimportable_core_module_raises(monkeypatch, tmp_path):
    """A declared module that fails to import aborts startup loudly."""
    bad = tmp_path / "defaults.yaml"
    bad.write_text("pipeline:\n  modules:\n    - adapt.no_such_module\n", encoding="utf-8")
    monkeypatch.setattr(pipeline_builder, "_DEFAULTS_YAML", bad)

    with pytest.raises(ImportError, match="adapt.no_such_module"):
        _ensure_modules_registered()


def _module_names_of(path: str) -> list[str]:
    mod = importlib.import_module(path)
    return [
        cls.name
        for _, cls in inspect.getmembers(mod, inspect.isclass)
        if issubclass(cls, BaseModule) and cls is not BaseModule and cls.__module__ == path
    ]


@pytest.mark.usefixtures("isolated_postprocess_catalog")
def test_catalog_lists_live_then_postprocess_modules_in_file_order():
    """The catalog is both discovery files, live first, each in its own order."""
    with open(_POSTPROCESS_DEFAULTS_YAML, encoding="utf-8") as f:
        post_paths = yaml.safe_load(f)["postprocess"]["modules"]
    expected = [
        name for path in [*_declared_module_paths(), *post_paths] for name in _module_names_of(path)
    ]

    assert catalog_module_names() == expected


@pytest.mark.usefixtures("isolated_postprocess_catalog")
def test_catalog_registers_every_module_it_lists():
    for name in catalog_module_names():
        assert name in registry


@pytest.mark.usefixtures("isolated_postprocess_catalog")
def test_catalog_entry_that_registers_no_module_raises(monkeypatch, tmp_path):
    """A discovery path must define a module; a stray path is a catalog typo."""
    stray = tmp_path / "defaults.yaml"
    stray.write_text("pipeline:\n  modules:\n    - adapt.execution.graph\n", encoding="utf-8")
    monkeypatch.setattr(pipeline_builder, "_DEFAULTS_YAML", stray)

    with pytest.raises(RuntimeError, match="adapt.execution.graph"):
        catalog_module_names()


def test_unreadable_postprocess_catalog_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline_builder, "_POSTPROCESS_DEFAULTS_YAML", tmp_path / "missing.yaml")

    with pytest.raises(RuntimeError, match="Cannot read post-process module list"):
        catalog_module_names()
