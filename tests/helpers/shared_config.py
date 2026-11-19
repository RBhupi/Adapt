# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The values a run's modules share, as a live run with default settings has them."""

from adapt.contracts import SharedConfig

SHARED = SharedConfig(
    tracking_field="reflectivity", z_level=2000.0, max_scan_gap_minutes=10.0, source="aws_nexrad"
)
