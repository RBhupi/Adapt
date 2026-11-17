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
module lines (`radar_command` acts on the decision `target_selection` makes, so
it needs it):

```yaml
modules:
  - ingest
  - detection
  - projection
  - analysis
  - tracking
  - cell_volume_stats
  - target_selection          # adapt config --show target_selection
  - radar_command             # adapt config --show radar_command
```

Check the wiring, then run:

```bash
adapt graph nexrad    # prints the chain: ingest -> ... -> tracking -> target_selection -> radar_command
adapt run nexrad
```

`adapt graph` only reads the file; paste its output into any Mermaid viewer to
see it as a diagram. `adapt run` checks the whole file before anything starts,
then prints one line per scan with the time each module took:

```
scan KHGX20261110_153521_V06  │  ingest 18.7s  detection 19ms  cell_volume_stats 1.2s  │  0 cells  19.9s
radar_command: command 1ba038bbaaf258f7 written for cell R0D4RLVKBZ (dry run, nothing sent)
scan KHGX20261110_154224_V06  │  ingest 18.5s  detection 19ms  projection 1.1s  analysis 29ms  tracking 11ms  cell_volume_stats 881ms  target_selection 12ms  radar_command 3ms  │  15 cells  20.5s
```

The first scan of a run is only read and segmented: projection, tracking and
everything after them need a previous scan, so they start with the second one
(and again after a gap between scans longer than `max_scan_gap_minutes`, 10 by
default, a `global` option under `params:`).

Every module's results are in the store. The two that matter here are tables
of the collection's `products.db`, read through `adapt.api.StoreClient` or the
dashboard:

| Table | What it holds |
|-------|---------------|
| `target_selection` | one row per decided scan: the chosen cell (`cell_uid`), or none, and why |
| `radar_commands` | one row per decision: the outcome (`PUBLISHED`, `NO_TARGET`, `TARGET_UNAVAILABLE`, ...) and the command itself (centre, extents, scan pattern) |

**Nothing is sent to a radar unless you ask for it.** As packaged, `radar_command`
is a dry run: commands are recorded in that table only (outcome `PUBLISHED`). To
send them, add an `api:` part under `params: radar_command:`
(`adapt config --show radar_command` prints it). The part holds no address, key
or path: it names three environment variables, and the environment is the only
source of what they hold. Export them before `adapt run`:

```bash
export BNF_API_URL=https://apigw.vsn.arm.gov/bnf   # commands are POSTed to $BNF_API_URL/publish/command
export ARM_API_CERT=/path/to/arm-ca.cer            # CA certificate (PEM) the API's certificate is checked against
read -rs BNF_RADAR_API_KEY && export BNF_RADAR_API_KEY   # type or paste the key; it is not shown or saved in history
```

A run whose file has `api:` refuses to start when one of them is unset, the URL
is not `https://`, or the CA file cannot be read. Each command is one request of
the form `{"AutoTarget": {...}}`, never repeated. Its console line starts with two
marks — did the command go out, and did the API acknowledge it:

| Marks | Outcome | Meaning |
|-------|---------|---------|
| `✓✓` | `SENT` | sent, and the API answered HTTP 200 with `status: published` on queue `scan_commands` and the submitted target back |
| `✓✗` | `NOT_ACKNOWLEDGED` | sent, but the API answered an error, something other than that acknowledgment, or nothing readable |
| `✗–` | `SEND_FAILED` | not sent: the API could not be reached, or its settings were lost |

The reason follows the marks; the whole of it is in the table's `detail` column.
`SENT` means the command is in the radar's queue, not that the radar ran it.

Only a fresh scan is sent. `api.max_scan_age_seconds` is how long ago, by this
machine's clock, a scan may have started when its command is ready to go out; an
older scan's command is recorded with outcome `STALE` and held back (no marks on
its line). This keeps the backlog a run starts with, and every scan of a
historical run, away from the radar.

A scan's time is the start of its volume, and its file exists only when the
volume ends, 4 to 7 minutes later. So the limit has to cover one volume, the
polling interval (`params: downloader: poll_interval_sec`, 300 s unless you set
it) and about 30 s of processing. A limit below one volume sends nothing; one
that covers the volume but not the polling interval sends only some scans. With
`poll_interval_sec: 30` a limit of 480 covers both radars measured so far
(commands ready 300 to 410 s after the scan's start).

If the run stops — `Ctrl-C`, a crash, a reboot — start it again with its id and
it goes on from the last complete scan, with the same result as an unbroken run:

```bash
adapt run nexrad --run-id 2026NOV10-1530-KHGX
```

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
processing the scans again, run the two modules over the stored run: see
*Running modules over a stored run* in the [CLI Reference](cli_reference.md).

### Changing options

The pipeline file lists only names, and a name alone runs with its defaults
(`target_selection` and `radar_command` with their packaged rule and command
setup). Set only what you change, under `params:` (e.g. `target_selection:` then
`  duration: {max_minutes: 15}`). Print a module's or section's options with their
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
`kdix.yaml`, edit it, and run `adapt run kdix` (or `adapt run kdix.yaml`: the
file may be named with its extension).

### Adding a module to a run you already have

Every step is a module in the file's one `modules:` list. A file with
`run.source: stored_run` runs its modules over a run the store already has
instead of over new scans — `adapt run NAME --run-id ID` — reading what they
need from the store: lightning statistics (`xlma_stat`) for a finished run, or
target selection again with another rule. See the
[CLI Reference](cli_reference.md).

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
└── collections/
    └── KLOT/                  # one collection per radar
        ├── catalog.db         # scans, artifacts, checksums, lineage
        ├── products.db        # cell stats, tracks, module tables, annotations
        ├── decisions.db       # the segmenter's and tracker's decision log (analysis only)
        └── objects/           # the files themselves, under readable names
            ├── raw/           # downloaded scans, under their original names
            └── <run_id>/<module>/   # what each module wrote in that run (checkpoint/: the state a resumed run goes on from)
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
