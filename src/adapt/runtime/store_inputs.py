# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Store-backed context inputs for post-process module execution.

Each provider materializes ONE context key from an existing store — data a
post-process module consumes that no selected module produces (mask geometry,
collection location, ...). The PostProcessor consults this registry on demand,
so adding a new store-backed input is one provider function plus one
``register_store_input`` call — the PostProcessor itself never changes.

Providers import their persistence readers lazily inside the function body so
heavy dependencies (xarray, pandas) load only when a module actually declares
the input.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from adapt.persistence.store import Collection
    from adapt.persistence.store_registry import StoreRegistry

__all__ = [
    "available_store_inputs",
    "register_store_input",
    "resolve_store_input",
    "unregister_store_input",
]

StoreInputProvider = Callable[["Collection", "StoreRegistry", str], Any]

_PROVIDERS: dict[str, StoreInputProvider] = {}


def register_store_input(key: str, provider: StoreInputProvider) -> None:
    """Register the provider that materializes ``key``; duplicates raise."""
    if not key:
        raise ValueError(f"Cannot register a store input with an empty key: {provider}")
    if key in _PROVIDERS:
        raise RuntimeError(
            f"Store input '{key}' is already registered by {_PROVIDERS[key]}. "
            f"Cannot register {provider} with the same key."
        )
    _PROVIDERS[key] = provider


def unregister_store_input(key: str) -> None:
    """Remove a provider (primarily for testing)."""
    _PROVIDERS.pop(key, None)


def available_store_inputs() -> tuple[str, ...]:
    """Sorted keys of every registered store input."""
    return tuple(sorted(_PROVIDERS))


def resolve_store_input(
    key: str, collection: "Collection", registry: "StoreRegistry", run_id: str
) -> Any:
    """Materialize one store input; an unknown key raises with the available set."""
    if key not in _PROVIDERS:
        raise KeyError(
            f"No store-input provider for '{key}'. Available: {', '.join(available_store_inputs())}"
        )
    return _PROVIDERS[key](collection, registry, run_id)


def _minute_masks(collection: "Collection", registry: "StoreRegistry", run_id: str) -> Any:
    from adapt.persistence.scan_mask_reader import read_minute_masks

    return read_minute_masks(collection, run_id)


def _projection_minute_masks(
    collection: "Collection", registry: "StoreRegistry", run_id: str
) -> Any:
    from adapt.persistence.scan_mask_reader import read_projection_minute_masks

    return read_projection_minute_masks(collection, run_id)


def _radar_origin(
    collection: "Collection", registry: "StoreRegistry", run_id: str
) -> tuple[float, float]:
    row = registry.get_collection(collection.collection_id)
    return (row["location_lat"], row["location_lon"])


register_store_input("minute_masks", _minute_masks)
register_store_input("projection_minute_masks", _projection_minute_masks)
register_store_input("radar_origin", _radar_origin)
