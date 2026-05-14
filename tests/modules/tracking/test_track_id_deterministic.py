import pytest

from adapt.modules.tracking.module import (
    _cell_uid_from_signature,
    _track_signature_from_birth,
)

pytestmark = pytest.mark.unit


def test_track_signature_format():
    sig = _track_signature_from_birth(
        scan_start_time_epoch_s=1700000000.0,
        centroid_lat_deg=35.04,
        centroid_lon_deg=-97.02,
        max_dbz=52.4,
        max_zdr=1.2,
        area40_km2=12.2,
        time_step_s=10,
        latlon_step_deg=0.1,
        area_step_km2=5.0,
    )
    assert sig.startswith("v1|")
    parts = sig.split("|")
    assert len(parts) == 7
    assert parts[0] == "v1"


def test_cell_uid_fixed_width_and_uppercase():
    sig = _track_signature_from_birth(
        scan_start_time_epoch_s=1700000000.0,
        centroid_lat_deg=35.01,
        centroid_lon_deg=-97.01,
        max_dbz=50.0,
        max_zdr=0.3,
        area40_km2=10.0,
        time_step_s=10,
        latlon_step_deg=0.1,
        area_step_km2=5.0,
    )
    pid = _cell_uid_from_signature(sig, width=10)
    assert len(pid) == 10
    assert pid == pid.upper()
    assert pid.isalnum()


def test_cell_uid_quantization_stability():
    sig_a = _track_signature_from_birth(
        scan_start_time_epoch_s=1700000000.0,
        centroid_lat_deg=35.01,
        centroid_lon_deg=-97.01,
        max_dbz=50.2,
        max_zdr=0.34,
        area40_km2=10.2,
        time_step_s=10,
        latlon_step_deg=0.1,
        area_step_km2=5.0,
    )
    sig_b = _track_signature_from_birth(
        scan_start_time_epoch_s=1700000002.0,
        centroid_lat_deg=35.04,
        centroid_lon_deg=-97.04,
        max_dbz=50.4,
        max_zdr=0.34,
        area40_km2=12.4,
        time_step_s=10,
        latlon_step_deg=0.1,
        area_step_km2=5.0,
    )
    pid_a = _cell_uid_from_signature(sig_a, width=10)
    pid_b = _cell_uid_from_signature(sig_b, width=10)
    assert pid_a == pid_b

    sig_c = _track_signature_from_birth(
        scan_start_time_epoch_s=1700000011.0,
        centroid_lat_deg=35.16,
        centroid_lon_deg=-97.16,
        max_dbz=51.6,
        max_zdr=0.56,
        area40_km2=20.2,
        time_step_s=10,
        latlon_step_deg=0.1,
        area_step_km2=5.0,
    )
    pid_c = _cell_uid_from_signature(sig_c, width=10)
    assert pid_a != pid_c
