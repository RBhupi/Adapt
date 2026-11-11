# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Geographic box around a target footprint. Pure: numpy + math only."""

import math
from dataclasses import dataclass

import numpy as np

EARTH_RADIUS_KM = 6371.0
_KM_PER_DEG = EARTH_RADIUS_KM * math.pi / 180


@dataclass(frozen=True)
class VerticalBounds:
    """Target vertical extent in metres AMSL; source names where it came from."""

    bottom_m: float
    top_m: float
    source: str


@dataclass(frozen=True)
class GeoExtent:
    """Lat/lon box (centre + full width, degrees) with altitude bounds (m AMSL).

    ``lon_center_deg`` is normalised to [-180, 180).
    """

    lat_center_deg: float
    lon_center_deg: float
    lat_width_deg: float
    lon_width_deg: float
    alt_bottom_m: float
    alt_top_m: float


def geographic_extent(
    lats: np.ndarray, lons: np.ndarray, margin_km: float, vertical: VerticalBounds
) -> GeoExtent:
    """Box over the points expanded by ``margin_km`` on all four sides.

    Longitudes are unwrapped relative to the first point, so a footprint across
    the antimeridian gives a narrow box. The longitude margin is sized at the
    poleward box edge, where a degree of longitude is shortest, so the margin is
    at least ``margin_km`` everywhere in the box.
    """
    _check_inputs(lats, lons, margin_km, vertical)
    lat_min, lat_max = float(lats.min()), float(lats.max())
    lon_min, lon_max = _unwrapped_range(lons)
    dlat = margin_km / _KM_PER_DEG
    phi_edge = max(abs(lat_min - dlat), abs(lat_max + dlat))
    if phi_edge >= 90.0:
        raise ValueError(
            f"geographic_extent: box edge at {phi_edge} deg latitude reaches a pole; "
            "a lat/lon box cannot contain it"
        )
    dlon = margin_km / (_KM_PER_DEG * math.cos(math.radians(phi_edge)))
    lon_width = lon_max - lon_min + 2 * dlon
    if lon_width >= 360.0:
        raise ValueError(
            f"geographic_extent: box longitude width {lon_width} deg reaches 360; "
            "it would wrap the globe"
        )
    return GeoExtent(
        lat_center_deg=(lat_min + lat_max) / 2,
        lon_center_deg=_normalise_lon((lon_min + lon_max) / 2),
        lat_width_deg=lat_max - lat_min + 2 * dlat,
        lon_width_deg=lon_width,
        alt_bottom_m=vertical.bottom_m,
        alt_top_m=vertical.top_m,
    )


def _check_inputs(
    lats: np.ndarray, lons: np.ndarray, margin_km: float, vertical: VerticalBounds
) -> None:
    if lats.shape != lons.shape:
        raise ValueError(
            f"geographic_extent: lats shape {lats.shape} != lons shape {lons.shape}; "
            "each point needs one latitude and one longitude"
        )
    if lats.size == 0:
        raise ValueError("geographic_extent: footprint is empty; a box needs at least one point")
    if not (np.isfinite(lats).all() and np.isfinite(lons).all()):
        raise ValueError("geographic_extent: footprint lat/lon must all be finite")
    if not (math.isfinite(margin_km) and margin_km >= 0):
        raise ValueError(f"geographic_extent: margin_km must be finite and >= 0, got {margin_km}")
    bottom, top = vertical.bottom_m, vertical.top_m
    if not (math.isfinite(bottom) and math.isfinite(top) and top > bottom):
        raise ValueError(
            f"geographic_extent: vertical ({vertical.source}) needs finite top_m > bottom_m, "
            f"got bottom_m={bottom}, top_m={top}"
        )


def _unwrapped_range(lons: np.ndarray) -> tuple[float, float]:
    """Min/max longitude after unwrapping into (ref - 180, ref + 180], ref = first point."""
    ref = float(lons.flat[0])
    offsets = np.mod(lons - ref, 360.0)  # [0, 360)
    unwrapped = ref + np.where(offsets > 180.0, offsets - 360.0, offsets)
    return float(unwrapped.min()), float(unwrapped.max())


def _normalise_lon(lon: float) -> float:
    """Map to [-180, 180); IEEE remainder is exact, so only +180 needs folding."""
    wrapped = math.remainder(lon, 360.0)
    return -180.0 if wrapped == 180.0 else wrapped
