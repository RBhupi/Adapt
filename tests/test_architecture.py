# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Architecture tests: enforce module-independence without hardcoding module names.

These tests discover adapt.modules subpackages at runtime and verify that
no scientific module imports from any other scientific module. New modules
are picked up automatically — no test edits required.

Run: pytest tests/test_architecture.py
"""

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

# Skip the entire file gracefully if adapt is not installed in this environment.
# This prevents VSCode pytest discovery errors when the wrong interpreter is active.
adapt_modules = pytest.importorskip(
    "adapt.modules",
    reason="adapt not installed in this Python environment — activate adapt_env",
)


def _discover_module_packages() -> list[str]:
    """Return all immediate subpackage names under adapt.modules."""
    return [
        f"adapt.modules.{info.name}"
        for info in pkgutil.iter_modules(adapt_modules.__path__)
        if info.ispkg
    ]


def _source_files(package_name: str) -> list[Path]:
    """Return all .py files belonging to a package."""
    mod = importlib.import_module(package_name)
    pkg_dir = Path(mod.__file__).parent
    return list(pkg_dir.rglob("*.py"))


def _imported_adapt_modules(py_file: Path) -> set[str]:
    """Parse a .py file and return the set of adapt.modules.* names it imports."""
    try:
        tree = ast.parse(py_file.read_text())
    except SyntaxError:
        return set()

    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("adapt.modules."):
                    imports.add(alias.name)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("adapt.modules.")
        ):
            imports.add(node.module)
    return imports


# Build the test matrix at collection time — works for any future module.
_PACKAGES = _discover_module_packages()
_SKIP = {"adapt.modules.base"}  # base.py is shared infrastructure, not a science module


@pytest.mark.parametrize("pkg", [p for p in _PACKAGES if p not in _SKIP])
def test_module_does_not_import_other_modules(pkg: str) -> None:
    """Scientific module must not import from any other adapt.modules subpackage.

    This test is parameterised over every subpackage discovered under adapt.modules.
    Adding a new module directory makes it appear here automatically.
    """
    files = _source_files(pkg)
    violations: list[str] = []

    for py_file in files:
        for imported in _imported_adapt_modules(py_file):
            # Allow self-imports (within the same subpackage)
            if not imported.startswith(pkg):
                violations.append(f"  {py_file.name}: imports {imported!r}")

    assert not violations, (
        f"\n{pkg} imports from other scientific modules — "
        "shared types belong in adapt.contracts:\n" + "\n".join(violations)
    )


@pytest.mark.parametrize("pkg", [p for p in _PACKAGES if p not in _SKIP])
def test_module_does_not_import_execution_or_runtime(pkg: str) -> None:
    """Scientific module must not import from adapt.execution or adapt.runtime."""
    forbidden_prefixes = ("adapt.execution", "adapt.runtime", "adapt.persistence")
    files = _source_files(pkg)
    violations: list[str] = []

    for py_file in files:
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(name.startswith(p) for p in forbidden_prefixes):
                    violations.append(f"  {py_file.name}: imports {name!r}")

    assert not violations, (
        f"\n{pkg} imports from layers above it — "
        "modules must only depend on contracts/ and utils/:\n" + "\n".join(violations)
    )
