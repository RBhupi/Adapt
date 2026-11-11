# User Guide

This guide covers running the Adapt pipeline, using the dashboard, understanding
outputs, and configuring the system. For installation see [Installation](installation.md).

---

## Quick start

Make a directory, initialize it as a data store, and work inside it. `adapt init`
creates the store layout (nothing else ever does — the pipeline refuses to run in
an uninitialized directory) and writes the pipeline file `nexrad.yaml` there.
The folder is the store, and every command reads it from the working directory,
so there are no paths to pass:

```bash
mkdir my_case && cd my_case
adapt init            # creates registry.db, logs/, collections/ and nexrad.yaml
```

Open `nexrad.yaml` and set `run.radar` to the 4-letter NEXRAD site code you want
(e.g. `KDIX`, `KFTG`, `KAMX`; the template uses `KLOT`).

Then, in **two terminals** with the conda environment active and both in that
directory:

**Terminal 1 — start the real-time pipeline:**

```bash
adapt run nexrad
```
The pipeline runs until you press `Ctrl-C`.

**Terminal 2 — open the dashboard:**

```bash
adapt dashboard
```

Click **Show Latest** in the dashboard to see the most recent processed scan.
Press `Ctrl-C` in Terminal 1 to stop the pipeline.

---

### Historical mode

Process a fixed time window from the archive: set the `run:` section of the
pipeline file, then `adapt run nexrad` as before.

```yaml
run:
  source: aws_nexrad
  radar: KLOT
  mode: historical
  start_time: 2025-03-05T18:00:00Z
  end_time: 2025-03-05T20:00:00Z
```

Times need a time zone; `Z` means UTC. The run stops by itself once every scan
in the window is processed.

### From scans to radar commands

The whole chain — read scans, find and track cells, choose a target, plan the
radar command — runs from one pipeline file. In `nexrad.yaml`, uncomment the two
consumer lines (`radar_command` acts on what `target_selection` writes, so it
needs it):

```yaml
consumers:
  target_selection:           # adapt config --show target_selection
  radar_command:              # adapt config --show radar_command
```

Check the wiring, then run:

```bash
adapt graph nexrad    # prints the chain: ingest -> ... -> tracking -> store -> target_selection -> radar_command
adapt run nexrad
```

`adapt graph` only reads the file; paste its output into any Mermaid viewer to
see it as a diagram. `adapt run` checks the whole file before anything starts,
then prints one line per scan with the time each module took:

```
scan KHGX20261110_153521_V06  │  ingest 18.7s  detection 19ms  │  0 cells  18.7s
scan KHGX20261110_154224_V06  │  ingest 18.5s  detection 19ms  projection 1.1s  analysis 29ms  tracking 11ms  cell_volume_stats 881ms  │  15 cells  20.5s
```

The first scan of a run is only read and segmented: projection, tracking and
everything after them need a previous scan, so they start with the second one
(and again after a gap between scans longer than `max_scan_gap_minutes`, 10 by
default, a `global` option under `params:`).

What to look at, all under `consumers/<run_id>/` in the folder:

| File | What it holds |
|------|---------------|
| `target_selection/decisions.jsonl` | one line per decided scan: the chosen cell, or why none |
| `target_selection/current_selection.json` | the newest decision |
| `radar_command/current_targets.json` | the newest radar command (centre, extents, scan pattern) |
| `radar_command/history/<plan_id>.json` | every command planned |
| `radar_command/history/activity.jsonl` | one event per step: `SELECTION_RECEIVED`, `PLAN_GENERATED`, `PUBLISHED`, `NO_TARGET`, ... |

**Nothing is sent to a radar unless you ask for it.** As packaged, `radar_command`
is a dry run: commands are written to those files only. To send them, add an
`api:` block under `radar_command:` (`adapt config --show radar_command` prints
it) and export the key in the environment variable it names before `adapt run`;
`SENT` or `SEND_FAILED` events then appear in `activity.jsonl`.

To try the chain on scans you already have on disk instead of downloading, point
the file at the folder; the run ends when every file in the window is processed:

```yaml
run:
  source: local_directory
  radar: KHGX
  mode: historical
  start_time: 2026-11-10T15:30:00Z
  end_time: 2026-11-10T16:10:00Z
  source_dir: /data/level2/KHGX     # folder of Level-II files, original names
```

To try other target-selection rules on a run you have already made, without
processing the scans again, replay it: see *Stored-run replay* in the
[CLI Reference](cli_reference.md).

### Changing options

The pipeline file lists only names, and a name alone runs with its defaults: a
listed module with its default options, an uncommented consumer line
(`target_selection:`, `radar_command:`) with its packaged preset. Set only what
you change: module options under `params:`, a consumer's under its own line
(e.g. `target_selection:` then `  duration: {max_minutes: 15}`). Print a module's or section's options with their
defaults and meanings, then paste what it prints under the file's `params:`
line, as its first line says (`params:` is the template's last line, so
`adapt config --show detection >> nexrad.yaml` does it too; an entry given twice
is refused):

```bash
adapt config --show detection   # the segmenter options detection uses
adapt config --show global      # settings shared by several modules
```

`adapt config nexrad` rewrites `nexrad.yaml` from the template (it asks before
overwriting). A store can hold several pipelines: copy `nexrad.yaml` to, say,
`kdix.yaml`, edit it, and run `adapt run kdix`.

### Consumers and post-processing

The `consumers:` section adds things that act on the results while the pipeline
runs — target selection, radar commands, plots — each writing under
`consumers/<run_id>/<consumer>/` in the store. The `postprocessing:` section
lists modules (e.g. `xlma_stat`) that run over the run once it completes. A file
with no `core:` modules replays a stored run instead: `adapt run NAME --run-id ID`,
writing into a new `consumers/<run_id>/replay-<n>/` each time. See the
[CLI Reference](cli_reference.md) for both.

### Verbose logging

Add `-v` to see debug-level output on the console as well as in the run's log,
including per-scan timing and any errors:

```bash
adapt run nexrad -v
```

### All options

Each command lists its own flags:

```bash
adapt --help
adapt run --help
adapt graph --help
adapt config --help
adapt dashboard --help
```

---

## Dashboard

Launch in a second terminal while the pipeline is running:

```bash
adapt dashboard
```

With no arguments it opens the working directory's repository if it is one, and
otherwise the repository you used last. Either way the toolbar's repository
selector switches to any other, so `--repo` is never required — it just
pre-populates that field:

```bash
adapt dashboard --repo /data/radar
```

A directory counts as a repository once `adapt init` has created `registry.db`
in it. Start the dashboard before the first scan is processed and the timeline
is simply empty until data arrives. Pre-store repositories (marked by the old
`adapt_registry.db`) are not readable — recreate them with `adapt init` and a
rerun.

The dashboard is **read-only** — it does not affect the pipeline.

### Controls

| Control | Description |
|---------|-------------|
| **Show Latest** | Jump to the most recent processed scan |
| **◄ / ►** | Step backward or forward one scan at a time |
| **Show Loop** | Animate the last N scans; set N and frame interval (ms) |
| **Variable** | Switch displayed field: reflectivity, ZDR, velocity, spectrum width |
| **Min / Max** | Set the colour-scale range; values outside are masked |
| **Proj steps** | Number of projected future positions to overlay (0 = show all) |
| **Hover** | Mouse over any cell to see its statistics in the side panel |

See [Dashboard Reference](dashboard_reference.md) for the full controls, launch wizard, and internals guide.

### Basemap

A background map overlay loads automatically if `contextily` is installed
(`pip install "arm-adapt[maps]"`). The first load fetches tiles from the
internet and may take a few seconds.

---

## Outputs — the data store

All pipeline artifacts live in the data store created by `adapt init` — the
folder you initialized and run `adapt run` from:

```
my_case/
├── nexrad.yaml                # the pipeline file (adapt init / adapt config nexrad)
├── registry.db                # runs (with their full config and pipeline file), collections, events
├── logs/                      # run log files
├── consumers/                 # consumer outputs: consumers/<run_id>/<consumer>/ (replays: replay-<n>/),
│                              # each run's or replay's folder holding a copy of its pipeline file
└── collections/
    └── KLOT/                  # one collection per radar
        ├── catalog.db         # scans, artifacts, checksums, lineage
        ├── products.db        # cell stats, tracks, module tables, annotations
        ├── decisions.db       # the segmenter's and tracker's decision log (analysis only)
        └── objects/           # the files themselves, under readable names
            ├── raw/           # downloaded scans, under their original names
            └── <run_id>/<module>/   # what each module wrote in that run
```

Files in `objects/` have readable names: a downloaded scan keeps its original
name (`raw/KLOT20250617_180012_V06`), and a file a module writes is named for the
scan it came from and what it holds, in a folder for the run and the module
(`<run_id>/ingest/KLOT20250617_180012_V06.gridded3d.nc`). The names are for
people: Adapt finds every file through the catalog, never by walking directories
or parsing names, and the catalog row carries the file's identity (`artifact_id`),
checksum and lineage. A run that processes a scan again replaces its own file;
a downloaded scan is never rewritten. Each scan is
identified by `scan_id`, the first 16 hex digits of the SHA-256 of the raw
Level-II file (verify externally with `sha256sum <raw file> | cut -c1-16`).
Every derived file records its checksum and its lineage back to that raw
volume, and every run records the exact configuration it ran with.

### Scan artifacts

Each scan produces a gridded 3-D NetCDF and a 2-D segmentation NetCDF
containing regridded radar fields, the cell label mask, and projected future
cell positions (optical flow). A scan is listed as complete only once all of
its required products exist, so readers never see half-written scans.

### Querying — StoreClient

Everything is queryable through the read-only [StoreClient API](api/client.rst):

```python
from adapt.api import StoreClient

client = StoreClient(".")  # or any store path
run = client.latest_run("KLOT")

cells = client.cells(run.run_id, "KLOT")            # all tracked cells
history = client.track_history(run.run_id, cells.iloc[0]["cell_uid"], "KLOT")

# Any module's table, with typed filters
big = client.table("cell_tracks", "KLOT", run_id=run.run_id,
                   filters={"max_reflectivity": {"op": "gt", "value": 50.0}})

# Arbitrary read-only SQL over products.db (catalog attached as `catalog`)
df = client.sql("SELECT COUNT(DISTINCT cell_uid) AS n FROM cells_by_scan", "KLOT")

client.close()
```

---

## Troubleshooting

### No data in dashboard after starting

The first scan takes longer (regridding + initial cell detection). Wait
30–60 seconds then click **Show Latest**.

### `No Adapt store at ...: registry.db not found`

Run `adapt init` in the directory first (the launch wizard in the dashboard
does this for you when starting a new pipeline).

### `... uses the obsolete pre-store layout (adapt_registry.db)`

The directory holds output from a pre-store version of Adapt. Move the old
data aside, run `adapt init`, and reprocess.

### Basemap not loading

`contextily` requires internet access to fetch map tiles. The first load for a
new area is slow. Check your network connection. Install if missing:

```bash
pip install "arm-adapt[maps]"
```

### Pipeline error on first scan

Re-run with `-v` to see the full traceback:

```bash
adapt run nexrad -v
```

### `adapt: command not found`

Activate the conda environment:

```bash
mamba activate adapt_env
```
