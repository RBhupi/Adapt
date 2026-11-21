# Results: the store, its tables and its files

This page is for someone who has made a run (see the [User Guide](USAGE.md))
and wants to know what it left on disk and how to read it. It lists the folders
of a store, the databases, every table with what one row of it is, the per-cell
view `cells_by_scan`, the NetCDF files, and the Python calls that read them.

The names used here (store, collection, run, scan, `cell_label`, `cell_uid`)
are explained under *The words Adapt uses* in the User Guide.

---

## The store folder

This is a store after one run of three scans of radar KHTX:

```
my_case/
├── nexrad.yaml                    # the pipeline file
├── registry.db                    # the runs of this store
├── logs/
│   └── pipeline_KHTX.log          # the log of the runs of this radar
└── collections/
    └── KHTX/                      # one collection per radar
        ├── catalog.db             # the scans and the files of this radar
        ├── products.db            # the results: tables and the view
        ├── decisions.db           # why detection and tracking decided as they did
        └── objects/               # the files
            ├── raw/               # the input scans, under their original names
            │   └── KHTX20260804_190053_V06
            └── 2026NOV18-2211-KHTX/        # one folder per run
                ├── ingest/
                │   └── KHTX20260804_190053_V06.gridded3d.nc
                ├── tracking/
                │   └── KHTX20260804_190655_V06.segmentation2d.nc
                └── checkpoint/             # what a resumed run goes on from
```

A file a module writes is named for the scan it came from and what it holds,
in a folder for the run and the module. The names are for people. Adapt itself
finds every file through `catalog.db`, which also holds each file's checksum.

The input scan is stored once, in `raw/`, whatever the number of runs that
process it. A run that processes a scan again replaces its own files.

| Database | Holds | Read it for |
|----------|-------|-------------|
| `registry.db` | one row per run, with its status and the full options it ran with; the warnings and errors of each run | finding a run id; seeing how a run was made |
| `catalog.db` | one row per scan of each run with its status; what each module did on each scan; one row per file | finding out why a scan has no result |
| `products.db` | the product tables and the view `cells_by_scan` | the science |
| `decisions.db` | every candidate detection and tracking looked at, and what became of it | studying the algorithms; not needed to use the results |

All four are SQLite files. Any SQLite tool reads them; so does the Python
`StoreClient` described at the end of this page.

---

## The product tables

A product table is written by one module. It exists in a collection from the
first time that module stores rows there.

| Table | Written by | One row per | Key |
|-------|-----------|-------------|-----|
| `cell_stats` | `analysis` | cell of a scan | `run_id`, `scan_id`, `cell_label` |
| `cell_adjacency` | `analysis` | pair of touching cells of a scan | `run_id`, `scan_id`, `cell_label_a`, `cell_label_b` |
| `cell_identity` | `tracking` | cell of a scan | `run_id`, `scan_id`, `cell_label` |
| `cell_events` | `tracking` | event between two scans | `run_id`, `scan_id`, `event_seq` |
| `cell_tracks` | `tracking` | track | `run_id`, `cell_uid` |
| `cell_volume_stats` | `cell_volume_stats` | cell of a scan | `run_id`, `scan_id`, `cell_label` |
| `target_selection` | `target_selection` | scan and rule | `run_id`, `scan_id`, `config_hash` |
| `radar_commands` | `arm_radar_comm` | scan and command setup | `run_id`, `scan_id`, `config_hash` |
| `xlma_stat_minutes` | `xlma_stat` | cell and minute with lightning (`cell_uid` = `UNATTRIBUTED` for flashes no cell was near) | `run_id`, `time`, `cell_uid` |
| `xlma_stat_scan` | `xlma_stat` | cell of a scan with lightning | `run_id`, `scan_id`, `cell_label` |

What every table has in common:

- `run_id` is in every row. A collection holds the rows of all its runs, so
  filter by `run_id`.
- A table with `scan_id` in its key also has `scan_time` (text, UTC,
  `2026-08-04T19:06:55Z`) and `scan_time_unix` (seconds since 1970).
- `config_hash` is a short code of the options that made a row. It keeps the
  decisions of two target selection rules apart in one table.
- A table keeps the columns of the first run that wrote it in the collection.
  A later run cannot add a column; see *Change an option* in the User Guide.

`client.tables("KHTX")` lists the tables a collection has, with the module and
the key of each.

---

## The per-cell view `cells_by_scan`

Most questions are about cells: how large, how intense, how tall, at each scan
of their life. The answers are in several tables, one per module. The view
`cells_by_scan` puts them side by side, so you read one table.

- It has **one row per tracked cell and scan**: the rows of `cell_identity`.
- It carries the columns of **every product table whose key is `run_id`,
  `scan_id`, `cell_label`**, whichever module wrote it. Today these are
  tracking's identity (`cell_uid`, the age, how the cell began), the statistics
  of `analysis` and, when the modules are listed, the volume statistics of
  `cell_volume_stats` and the per-scan lightning of `xlma_stat` (its columns
  start with `xlma_`).
- It adds three flags that tracking computes from the events of the following
  scan: `is_split_source_here`, `is_merge_source_here` and
  `is_terminated_after_here` (the cell split, merged into another, or ended
  after this scan).

Nothing has to be edited for a new table to appear in the view. A module that
is run later over a stored run, or a new module, shows up there as soon as it
stores a table with that key. For this to work a column name is used only
once across those tables, whatever its letter case: a table that repeats a
name is refused when it is first written.

A store written by an earlier version keeps its narrower view until a run of
this version writes to that store; then the view is rebuilt for every run in
the collection.

Two things to know when you read it:

- A cell that a module did not measure has empty values in that module's
  columns.
- The cells of the first scan of a run are not in the view. Tracking starts
  with the second scan, so they have no `cell_uid`. Their volume statistics
  are in `cell_volume_stats`, by `cell_label`.
- The lightning columns (`xlma_flash_count`, `xlma_n_minutes`, ...) are filled
  for a cell that had lightning in the minutes before the scan, and empty for
  a cell that had none. The minutes before a scan are followed with the cells
  of the scan before it: lightning of a cell that is no longer in the scan
  (its track ended or merged into another) is not in the view. It is in
  `xlma_stat_minutes`, which holds every attributed flash, by `cell_uid`.

```sql
SELECT scan_time, cell_uid, cell_area_sqkm, radar_reflectivity_max, cell_top_m
FROM cells_by_scan
WHERE run_id = '2026NOV18-2211-KHTX'
ORDER BY cell_uid, scan_time;
```

---

## The columns you will use most

Column names are fixed for the radar variables Adapt knows; see
[Variable Names](variable_names.md). The full list of a table's columns is in
the database itself (`PRAGMA table_info(cell_stats)` in SQLite,
`client.tables(...)` in Python).

### Identity, from `tracking` (in `cells_by_scan`)

| Column | Meaning |
|--------|---------|
| `cell_uid` | the identity of the cell across scans |
| `cell_label` | its number in this scan; the value of its pixels in the NetCDF label field |
| `age_seconds` | time since the track was first seen |
| `is_initiated_here` | 1 when the track begins at this scan |
| `is_split_target_here`, `is_merge_target_here` | 1 when the cell came out of a split, or absorbed another cell, at this scan |
| `merged_from_cell_uid`, `merged_from_age_seconds` | the cell that was absorbed, and the age it had reached |
| `n_adjacent_cells`, `adjacent_cell_uids_json` | the cells touching this one |

### Statistics of the slice, from `analysis` (in `cells_by_scan`)

| Column | Meaning |
|--------|---------|
| `cell_area_sqkm` | area of the cell (km²) |
| `area_40dbz_km2` | area of the cell above 40 dBZ (km²) |
| `radar_<variable>_mean`, `_min`, `_max` | statistics of each radar variable over the cell, for example `radar_reflectivity_max` (dBZ) and `radar_differential_reflectivity_mean` (dB) |
| `cell_centroid_geom_lat`, `cell_centroid_geom_lon` | geometric centre of the cell |
| `cell_centroid_mass_lat`, `_lon` | reflectivity-weighted centre |
| `cell_centroid_maxdbz_lat`, `_lon` | place of the reflectivity maximum |
| `cell_centroid_projection1_lat`, `_lon` (and `2`, `3`) | where the centre is projected to be one, two and three scan steps ahead |
| `cell_heading_x_mean`, `cell_heading_y_mean` | mean motion of the cell, in grid points per scan step |

Every centroid also has `_x` and `_y` columns: its position as grid indices.

### Statistics of the volume, from `cell_volume_stats` (in `cells_by_scan`)

The volume of a cell is its outline in the slice, extended through all
altitudes of the grid.

| Column | Meaning |
|--------|---------|
| `cell_volume_km3` | volume with echo (km³) |
| `vol_20dbz_km3` … `vol_60dbz_km3` | volume at or above 20, 30, 40, 50, 60 dBZ |
| `cell_base_m`, `cell_top_m`, `cell_depth_m` | lowest and highest grid level with echo, and their difference (m) |
| `dbz_max`, `dbz_mean` | reflectivity over the volume (dBZ) |
| `dbz_max_height_m`, `dbz_com_height_m` | height of the maximum, and of the reflectivity-weighted centre (m) |
| `cell_eth<T>_top_max`, `_mean`, `_min` | echo-top height at threshold T dBZ over the columns of the cell (m); T is 10, 20, 30, 40, 50 by default. `_bottom_` and `_depth_` columns give the base and the depth of the same layer |
| `zdr_max`, `zdr_mean`, `rhohv_mean`, … | differential reflectivity and correlation coefficient over the volume |

### Tracks, in `cell_tracks`

| Column | Meaning |
|--------|---------|
| `cell_uid` | the track |
| `first_seen_time`, `last_seen_time`, `n_scans`, `duration_seconds` | when it was seen, and for how long |
| `origin_type` | how it began: `INITIATION`, `SPLIT`, or `UNKNOWN` |
| `termination_type` | how it ended: `TERMINATION`, `MERGED`, or `ACTIVE_AT_END` when it was still there at the last scan processed |
| `terminated_into_cell_uid` | the cell it merged into |
| `max_area_sqkm`, `max_reflectivity` | the largest area and the largest value of the tracked field over its life |

### Events, in `cell_events`

One row per event, with its `event_type`: `CONTINUE`, `SPLIT`, `MERGE`,
`INITIATION`, `TERMINATION`, `LATENT` (a track that has lost its cell and is
kept for a few scans in case it comes back) and `RESUMED`. An event that links
two cells names the cell of the earlier scan (`source_cell_uid`) and the cell
of this scan (`target_cell_uid`).

---

## The decision log (`decisions.db`)

`decisions.db` holds, for every scan, what detection and tracking weighed and
why: each seed and carried claim, each candidate pair of cells, each split or
merge hypothesis. Nothing in Adapt reads it, and `StoreClient` does not list
it; open it with any SQLite tool.

| Table | Written by | One row per | Key |
|-------|-----------|-------------|-----|
| `segmentation_frames` | `detection` | scan | `run_id`, `scan_id` |
| `segmentation_seeds` | `detection` | seed or carried claim, numbered in order | `run_id`, `scan_id`, `seq` |
| `segmentation_components` | `detection` | component of the convective mask | `run_id`, `scan_id`, `component_id` |
| `tracking_scans` | `tracking` | scan after the first | `run_id`, `scan_id` |
| `tracking_cells` | `tracking` | cell of a scan | `run_id`, `scan_id`, `cell_label` |
| `tracking_pairs` | `tracking` | candidate pair of a previous and a current cell | `run_id`, `scan_id`, `prev_cell_uid`, `curr_cell_label` |
| `tracking_lineage` | `tracking` | hypothesis for a left-over cell, numbered in order | `run_id`, `scan_id`, `seq` |
| `tracking_identity` | `tracking` | contender for a split or merged identity | `run_id`, `scan_id`, `kind`, `identity_uid`, `candidate_side`, `candidate_label` |
| `tracking_latent` | `tracking` | latent track at a scan | `run_id`, `scan_id`, `cell_uid` |

- A row holds the fields of one decision record of the module, with
  `run_id`, `scan_id`, `scan_time` and `scan_time_unix` added, as in every
  product table. The columns are documented on the record types, in
  `adapt.contracts.decisions`.
- Thresholds are not repeated here: join `run_id` to the run's record
  (`registry.db`) for the options the run used.
- A table exists from its first row. `segmentation_frames` and
  `tracking_scans` get a row at every scan their module runs; a table such as
  `tracking_identity` appears only with the first contested split or merge, so
  a query on it before then gets "no such table".
- `table_schemas` lists each table with its module, its key and its columns.
- A `decisions.db` written before modules declared their decision tables
  (Adapt versions before this one) is refused when a run opens the collection.
  Move `collections/<RADAR>/decisions.db` aside; it stays readable, and the
  run makes a new one.

---

## The NetCDF files

| File | Written by | Per scan | Holds |
|------|-----------|----------|-------|
| `<scan>.gridded3d.nc` | `ingest` | every scan | the radar variables on the 3-D grid (`time`, `z`, `y`, `x`), for example `reflectivity`, `velocity`, `differential_reflectivity`, `cross_correlation_ratio`, `spectrum_width` |
| `<scan>.segmentation2d.nc` | `tracking` | every scan from the second | the same variables on the slice (`y`, `x`), and the fields below |

Fields of the segmentation file:

| Variable | Meaning |
|----------|---------|
| `cell_labels` | the cell number of each grid point; 0 is no cell |
| `cell_uid` | the `cell_uid` of each cell number of this scan |
| `heading_x`, `heading_y` | the motion field, in grid points per scan step |
| `cell_projections` | cell labels moved by the motion, one layer per `frame_offset`: layer 0 is the previous scan's cells moved to this scan's time, layers 1 and up are this scan's cells moved one scan step further each |
| `projection_minutes` | this scan's cell labels moved forward to each whole minute after it |
| `registration_minutes` | the previous scan's cell labels moved to each whole minute between the two scans |
| `registration_cell_uid` | the `cell_uid` of each cell number of the previous scan |

The grid coordinates `x` and `y` are in metres from the radar; `lat` and `lon`
give the position of each grid point. Each file records the `scan_id` and the
`scan_time` it belongs to in its attributes.

With the default grid a gridded volume is about 120 MB and a segmentation file
about 15 MB.

---

## Reading with Python: `StoreClient`

`adapt.api.StoreClient` is the supported way to read a store from Python. It
is read-only, apart from the tags you add with `annotate`. Tables come back as
pandas tables, files as xarray datasets. You never build a path.

```python
from adapt.api import StoreClient

with StoreClient("/path/to/my_case") as client:
    run = client.latest_run("KHTX")
    cells = client.cells(run.run_id, "KHTX")
```

| Call | Returns |
|------|---------|
| `client.collections()` | the radars of the store |
| `client.runs("KHTX")`, `client.latest_run("KHTX")` | the runs, newest first; the newest |
| `client.run_config(run_id)` | the options the run was made with |
| `client.scans("KHTX", run_id=...)` | the complete scans of a run, in time order |
| `client.tables("KHTX")` | the product tables of the collection |
| `client.cells(run_id, "KHTX")` | `cells_by_scan` for one run |
| `client.cells_at_scan(run_id, scan_id, "KHTX")` | the same for one scan |
| `client.track_history(run_id, cell_uid, "KHTX")` | the same for one cell, in time order |
| `client.tracks("KHTX", run_id)` | `cell_tracks` |
| `client.track_events(run_id, cell_uid, collection="KHTX")` | the events of a run, or of one cell |
| `client.track_graph(run_id, cell_uid, "KHTX")` | every cell connected to one cell by splits and merges, with the events |
| `client.table(name, "KHTX", run_id=..., filters=...)` | any product table, filtered |
| `client.sql(query, "KHTX")` | the result of any `SELECT` |
| `client.open_scan_raster("KHTX", run_id, scan_id)` | the segmentation file of a scan, in memory; `product="gridded3d"` for the volume |
| `client.scan_timeline("KHTX", run_id, "segmentation2d")` | the scans that have that file, in time order |

A filter is a value (equal to), a list (one of), or a comparison:

```python
strong = client.table(
    "cell_tracks", "KHTX", run_id=run.run_id,
    filters={"max_reflectivity": {"op": "gt", "value": 50.0}},   # eq, gt, ge, lt, le, in
)
```

`client.sql` runs on `products.db`, with `catalog.db` attached under the name
`catalog`, so one query can join results with the scans:

```python
n = client.sql("SELECT COUNT(DISTINCT cell_uid) AS n FROM cells_by_scan", "KHTX")
scans = client.sql("SELECT scan_id, scan_time, status FROM catalog.scans", "KHTX")
```

Reading a scan's fields:

```python
timeline = client.scan_timeline("KHTX", run.run_id, "segmentation2d")
with client.open_scan_raster("KHTX", run.run_id, timeline[-1].scan_id) as raster:
    labels = raster.dataset["cell_labels"]
```

The full list of calls is in the [StoreClient API](api/client.rst).

---

## What a run did, scan by scan

When a scan has no cells or a table has fewer rows than you expect, the store
says why.

```bash
# the runs of the store
sqlite3 registry.db "SELECT run_id, status, started_at, ended_at FROM runs"

# the scans of a run and their status: complete, failed or pending
sqlite3 collections/KHTX/catalog.db \
  "SELECT scan_time, scan_id, status FROM scans WHERE run_id = '2026NOV18-2211-KHTX'"

# what each module did on each scan: done, skipped or failed, with the reason
sqlite3 collections/KHTX/catalog.db \
  "SELECT scan_id, module, status, reason FROM scan_modules WHERE run_id = '2026NOV18-2211-KHTX'"

# the warnings and errors of a run
sqlite3 registry.db "SELECT severity, module, message FROM run_events WHERE run_id = '2026NOV18-2211-KHTX'"

# the options each module ran with, and the Adapt version that ran it
sqlite3 registry.db \
  "SELECT module, options_hash, version, options_json FROM run_module_provenance WHERE run_id = '2026NOV18-2211-KHTX'"
```

The last table has one row per module and set of options: a module added later
by a run over the stored run is there too. For `target_selection` and
`arm_radar_comm`, `options_hash` is the `config_hash` in their rows, so a row's
hash leads to the rules that made it.

On the first scan of a run the second query of modules shows, for example:

```
144fdb67058bd48a|projection|skipped|needs 1 prior scan(s), 0 usable
144fdb67058bd48a|analysis|skipped|needs 'projection', which was skipped
```

The same messages, with everything else the run logged, are in
`logs/pipeline_<RADAR>.log`.
