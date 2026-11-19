# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Unit tests for resolve_enabled_modules — pure module-selection logic."""

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

from adapt.execution.pipeline_builder import resolve_enabled_modules  # noqa: E402


def _mods():
    """A linear synthetic pipeline: a -> b -> c, plus an independent ext."""
    return [
        SimpleNamespace(name="a", inputs=["raw"], outputs=["x"]),
        SimpleNamespace(name="b", inputs=["x"], outputs=["y"]),
        SimpleNamespace(name="c", inputs=["y"], outputs=["z"]),
        SimpleNamespace(name="ext", inputs=["raw"], outputs=["w"]),
    ]


def _names(mods):
    return [m.name for m in mods]


def test_modules_allowlist_restricts_and_preserves_order():
    out = resolve_enabled_modules(_mods(), modules=["ext", "a", "b", "c"])
    assert _names(out) == ["a", "b", "c", "ext"]  # original order, not arg order


def test_empty_allowlist_enables_nothing():
    assert resolve_enabled_modules(_mods(), modules=[]) == []


def test_unknown_name_raises():
    with pytest.raises(ValueError, match="Unknown module 'nope'"):
        resolve_enabled_modules(_mods(), modules=["nope"])


def test_disabling_leaf_is_allowed():
    # 'c' produces 'z' which nothing consumes → safe to leave out.
    assert _names(resolve_enabled_modules(_mods(), modules=["a", "b", "ext"])) == ["a", "b", "ext"]
