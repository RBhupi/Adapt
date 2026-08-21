# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Store-input provider registry: the extensibility seam for post-process inputs."""

import pytest

from adapt.runtime import store_inputs

pytestmark = pytest.mark.unit


def test_register_and_resolve_custom_provider():
    store_inputs.register_store_input("probe_input", lambda col, reg, run_id: {"run": run_id})
    try:
        value = store_inputs.resolve_store_input("probe_input", None, None, "RUN1")
        assert value == {"run": "RUN1"}
    finally:
        store_inputs.unregister_store_input("probe_input")


def test_duplicate_registration_raises():
    store_inputs.register_store_input("probe_dup", lambda col, reg, run_id: 1)
    try:
        with pytest.raises(RuntimeError, match="probe_dup"):
            store_inputs.register_store_input("probe_dup", lambda col, reg, run_id: 2)
    finally:
        store_inputs.unregister_store_input("probe_dup")


def test_empty_key_raises():
    with pytest.raises(ValueError, match="empty"):
        store_inputs.register_store_input("", lambda col, reg, run_id: 1)


def test_unknown_key_raises_listing_available():
    with pytest.raises(KeyError, match="minute_masks"):
        store_inputs.resolve_store_input("no_such_key", None, None, "RUN1")


def test_builtin_providers_are_registered():
    available = store_inputs.available_store_inputs()
    assert "minute_masks" in available
    assert "projection_minute_masks" in available
    assert "radar_origin" in available
