from adapt.persistence.registry import RepositoryRegistry


def test_registry_ensure_radar_location_populates_missing(tmp_path):
    registry = RepositoryRegistry.get_instance(tmp_path)
    registry.register_radar("KPOE", lat=None, lon=None)

    lat0, lon0 = registry.get_radar_location("KPOE")
    assert lat0 is None
    assert lon0 is None

    registry.ensure_radar_location("KPOE", lat=31.155277252197266, lon=-92.97611236572266)

    lat1, lon1 = registry.get_radar_location("KPOE")
    assert lat1 == 31.155277252197266
    assert lon1 == -92.97611236572266


def test_registry_ensure_radar_location_is_idempotent(tmp_path):
    registry = RepositoryRegistry.get_instance(tmp_path)
    registry.register_radar("KPOE", lat=31.0, lon=-92.0)

    registry.ensure_radar_location("KPOE", lat=31.155277252197266, lon=-92.97611236572266)

    lat, lon = registry.get_radar_location("KPOE")
    assert lat == 31.0
    assert lon == -92.0
