# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""What the runtime reads of a scan's results.

A module reports a fact the runtime needs about a scan as an ordinary output,
under a key named here; the runtime reads the key and never a module's dataset
or table. At most one listed module reports each key.

- ``radar_location``: where the radar of the scan is (a ``RadarLocation``);
  recorded once for the collection.
- ``scan_cell_count``: the number of cells the scan is reported with (an
  ``int``, zero or more); printed on the scan's line and summed for the run.
"""

import math
from dataclasses import dataclass
from typing import Any

from adapt.contracts.pipeline import require

RADAR_LOCATION = "radar_location"
SCAN_CELL_COUNT = "scan_cell_count"


@dataclass(frozen=True)
class RadarLocation:
    """Where the radar of a scan is."""

    latitude: float  # degrees north
    longitude: float  # degrees east


def check_radar_location(location: Any) -> None:
    """Bound contract for ``radar_location``: a ``RadarLocation`` on the globe."""
    require(
        isinstance(location, RadarLocation),
        f"radar_location must be a RadarLocation, got {type(location)}",
    )
    for name in ("latitude", "longitude"):
        value = getattr(location, name)
        require(
            isinstance(value, float) and math.isfinite(value),
            f"radar_location.{name} must be a finite float, got {value!r}",
        )
    require(
        -90.0 <= location.latitude <= 90.0,
        f"radar_location.latitude {location.latitude} is outside -90 to 90",
    )
    require(
        -180.0 <= location.longitude <= 360.0,
        f"radar_location.longitude {location.longitude} is outside -180 to 360",
    )


def check_scan_cell_count(count: Any) -> None:
    """Bound contract for ``scan_cell_count``: a whole number of cells, zero or more."""
    require(
        isinstance(count, int) and not isinstance(count, bool),
        f"scan_cell_count must be an int, got {type(count)}",
    )
    require(count >= 0, f"scan_cell_count must be zero or more, got {count}")
