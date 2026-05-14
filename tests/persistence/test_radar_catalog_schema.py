import sqlite3

from adapt.persistence.catalog import RadarCatalog


def test_radar_catalog_initializes_track_tables(tmp_path):
    radar_dir = tmp_path / "KPOE"
    radar_dir.mkdir()

    catalog = RadarCatalog(radar_dir)
    conn = sqlite3.connect(catalog.db_path)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    catalog.close()

    assert {
        "items",
        "progress",
        "schemas",
        "scans",
        "cells_by_scan",
        "cell_events",
        "cell_tracks",
    }.issubset(tables)
