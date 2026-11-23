# User Guide

This guide is for a scientist who knows weather radar and has never used Adapt.
It assumes Adapt is installed ([Installation](installation.md)) and that you can
use a terminal. It takes you from an empty folder to tracked storm cells in
tables and files, then shows how to change what a run does. The detail of every
command is in the [CLI Reference](cli_reference.md); the detail of every table
and file is in [Results](outputs.md).

---

## What Adapt does

Adapt reads a sequence of radar volume scans, one after the other. For each
scan it:

1. puts the volume on a regular grid;
2. finds convective cells in a horizontal slice of that grid;
3. estimates how the cells moved since the previous scan, and where they will be
   in the next minutes;
4. measures each cell (area, reflectivity, height, and more);
5. links each cell to the cells of the previous scan, so a storm keeps one
   identity for its whole life, through splits and merges.

Two further steps are optional: choosing the cell a radar should follow, and
writing the command that points the radar at it.

What you get:

| Output | Form |
|--------|------|
| The gridded volume of every scan | one NetCDF file per scan |
| The slice with the cell labels, the motion field and the projected cell positions | one NetCDF file per scan |
| One row per cell and scan: identity, statistics, volume statistics | SQLite tables, read together through the view `cells_by_scan` |
| One row per track: first and last time, how it began and ended | SQLite table `cell_tracks` |
| One row per event: a cell continues, splits, merges, begins, ends | SQLite table `cell_events` |
| What every run was made with, and what each step did on each scan | SQLite tables |

The same steps run on files you already have, on the public NEXRAD archive, and
on scans as they arrive.

## The words Adapt uses

| Word | Meaning |
|------|---------|
| **Store** | The folder that holds everything: the pipeline files, the databases and the output files. `adapt init` makes a folder a store. Every command is run from inside it. Some messages and the dashboard call it the *repository*. |
| **Collection** | The part of a store that belongs to one radar: the folder `collections/<RADAR>/`. |
| **Scan** | One radar volume, from one input file. Its `scan_id` is the first 16 hexadecimal digits of the SHA-256 of that file, so the same file has the same `scan_id` in every run. |
| **Pipeline file** | A YAML file in the store, `NAME.yaml`. It says where the scans come from, which modules run, and the options you change. `adapt init` writes one, `nexrad.yaml`. |
| **Module** | One step of the processing: `ingest`, `detection`, `projection`, `analysis`, `tracking` and the others listed under *The modules* below. A pipeline file lists the modules to run. |
| **Run** | One execution of a pipeline file. Its id is `YYYYMONDD-HHMM-RADAR` (`2026NOV18-2211-KHTX`): the local time it started, and the radar. A store keeps every run; rows and files carry the `run_id`. |
| **Cell** | A connected area of echo that detection labels in one scan. With the defaults: reflectivity above 35 dBZ at 2 km altitude, at least 5 grid points. |
| **`cell_label`** | The number of a cell within its scan (1 is the largest cell). It means nothing in another scan. |
| **`cell_uid`** | The identity tracking gives a cell: ten characters such as `UDX00RVWU3`, the same in every scan the cell is seen in. |
| **Track** | All the scans of one `cell_uid`. |
| **Product table** | A table of results that a module writes, in the collection's `products.db`. |
| **View** | A table that is computed from other tables when you read it. `cells_by_scan` is the one view. |

---

## First run: scans on your disk

You need a folder of NEXRAD Level-II files of one radar, with their original
names (`KHTX20260804_190053_V06`). If you have none, go to
*Run on the public NEXRAD archive* below.

1. Make a folder and turn it into a store.

   ```bash
   mkdir my_case && cd my_case
   adapt init
   ```

   This creates `registry.db`, `logs/`, `collections/` and the pipeline file
   `nexrad.yaml`. Stay in this folder for every command that follows.

2. Open `nexrad.yaml` and set its `run:` section to your radar, your folder and
   the time window to process. Leave the rest of the file as it is.

   ```yaml
   run:
     source: local_directory
     radar: KHTX
     mode: historical
     start_time: 2026-08-04T19:00:00Z
     end_time: 2026-08-04T19:15:00Z
     source_dir: /data/level2/KHTX
     max_runtime_minutes: null
   ```

   Times need a time zone; `Z` is UTC. Every file in `source_dir` must be a
   volume of `radar`, named `RADARYYYYMMDD_HHMMSS...` (hidden files are
   ignored); a folder with any other file is refused. Only the files inside the
   window are processed.

3. Run it.

   ```bash
   adapt run nexrad
   ```

   The run checks the whole file first, then processes the scans in time order
   and stops by itself after the last one.

With three scans the console shows this (the time stamp that starts each log
line is left out here):

```
── Adapt run 2026NOV18-2211-KHTX ─────────────────────────────
site KHTX · NEXRAD · mode historical
...
scan KHTX20260804_190053_V06  │  ingest 14.3s  detection 26ms  cell_volume_stats 376ms  │  0 cells  14.7s
scan KHTX20260804_190655_V06  │  ingest 14.0s  detection 24ms  projection 655ms  analysis 57ms  tracking 24ms  cell_volume_stats 429ms  │  36 cells  15.2s
scan KHTX20260804_191250_V06  │  ...  │  35 cells  45.7s
── run 2026NOV18-2211-KHTX  SUCCESS  46s ────────────────────
files 3 · scans 3 · objects 71 · warnings 1 · errors 0 · failures 0
```

How to read it:

- The first line gives the **run id**. Note it: you need it to resume the run
  or to run more modules over it later.
- There is one line per scan, with the time each module took and the number of
  cells that `analysis` measured. A pipeline or pass that does not list
  `analysis` shows no cell count.
- **The first scan has 0 cells, and that is correct.** Motion needs two scans,
  so `projection`, and `analysis` and `tracking` which read from it, start with
  the second scan. The same happens after a gap between two scans longer than
  `max_scan_gap_minutes` (10 by default). A run of N scans gives N−1 scans of
  tracked cells.
- The last block is the summary. The command exits with `0` when the run
  completed and no scan failed.

Each scan took about 15 seconds on the laptop this guide was checked on, nearly
all of it gridding. With the default grid a scan also takes about 30 MB of
disk: the radar file as it came, and the gridded and segmentation files, which
are stored compressed and so grow with the amount of echo. Plan for about
0.3 GB per hour of scans.

---

## Look at the results

Everything is in the store. There are three ways to read it; [Results](outputs.md)
describes every table, column and file.

**From Python**, through the read-only `StoreClient`:

```python
from adapt.api import StoreClient

with StoreClient(".") as client:          # "." is the store folder
    run = client.latest_run("KHTX")       # the newest run of this radar
    print(run.run_id, run.status)

    cells = client.cells(run.run_id, "KHTX")     # one row per cell and scan
    print(cells[["scan_time", "cell_uid", "cell_area_sqkm",
                 "radar_reflectivity_max", "cell_top_m"]].head())

    tracks = client.tracks("KHTX", run.run_id)   # one row per track
    one = client.track_history(run.run_id, cells.iloc[0]["cell_uid"], "KHTX")
```

`cells` is the view `cells_by_scan` as a pandas table: the tracked identity of
each cell with all the statistics the modules of the run computed.

**With SQL**, for example with the `sqlite3` command:

```bash
sqlite3 collections/KHTX/products.db \
  "SELECT scan_time, cell_uid, cell_area_sqkm, radar_reflectivity_max
   FROM cells_by_scan WHERE run_id = '2026NOV18-2211-KHTX' ORDER BY scan_time LIMIT 5"
```

**In the dashboard**, a window that shows each scan as a map with its cells,
and time series of the cells you click:

```bash
adapt dashboard
```

Run it from the store folder. See *Dashboard* below.

The files are under `collections/KHTX/objects/<run_id>/`: the gridded volumes in
`ingest/`, the slices with labels and motion in `tracking/`.

---

## Run on the public NEXRAD archive

Adapt can fetch the scans itself from the public NEXRAD Level-II archive on AWS
(the bucket `unidata-nexrad-level2`; no account is needed, only internet
access). Each scan is downloaded once into `collections/<RADAR>/objects/raw/`
and then processed.

**A fixed time window.** Set the `run:` section like this and run
`adapt run nexrad`; the run stops when every scan of the window is processed.

```yaml
run:
  source: aws_nexrad
  radar: KLOT
  mode: historical
  start_time: 2025-03-05T18:00:00Z
  end_time: 2025-03-05T20:00:00Z
  source_dir: null
  max_runtime_minutes: null
```

**As the scans arrive.** This is what `nexrad.yaml` does as `adapt init` writes
it, for radar `KLOT`: set `radar` to your 4-letter site code and run it.

```yaml
run:
  source: aws_nexrad
  radar: KLOT
  mode: realtime
  start_time: null
  end_time: null
  source_dir: null
  max_runtime_minutes: null
```

A realtime run first checks that the archive has data for the radar today or
yesterday, so a misspelled site code fails at once. It starts with the newest
scans of the last hour (up to 5), then looks for each new volume by the age of
the last one the archive published: not before 200 seconds, then every 20
seconds, every 10 seconds from 300 seconds, and every 30 seconds once the volume
is late (450 seconds). Each look lists only the files added since the last one.
It runs until you press `Ctrl-C`, or for `max_runtime_minutes` when you set it.
The numbers are options of `aws_nexrad` (`adapt config --params aws_nexrad`).

**When processing falls behind.** A realtime run keeps current rather than
complete. While a scan is still waiting to be processed, only the newest new
volume is kept; each one passed over is logged as `Skipped`. Skipped volumes
break the tracks, so after `max_skipped_scans` of them (3 unless you set it) the
run stops and is recorded as `failed`. Restarting it begins new tracks; to do
that automatically, run it in a loop:

```bash
until adapt run nexrad; do sleep 10; done
```

The loop restarts the run after any failure, a misspelled radar too, so check
the first run before you leave it unattended.

---

## Change an option

The pipeline file lists only names; everything else runs with its default. To
change an option, give it under `params:`, the last line of the file, under the
name of the module it belongs to. Give only what you change.

1. Print the options of a module, with their defaults and meanings:

   ```bash
   adapt config --params detection tracking   # one module or several
   adapt config --params global               # settings shared by several modules
   ```

2. Write the ones you change under `params:`. Or let Adapt write every option
   of the modules the file lists, at its default, and edit the values there:

   ```bash
   adapt config --params --file nexrad.yaml             # every module the file lists
   adapt config --params --file nexrad.yaml tracking    # one module, listed or not
   ```

   It only adds what the file does not give: your values and comments stay,
   and running it again changes nothing.

   ```yaml
   params:
     global:
       max_scan_gap_minutes: 12
     detection:
       threshold_params: {threshold: 40}   # one key of a nested part, given alone
     tracking:
       max_speed_ms: 35
   ```

3. Check the file without running it, then run it.

   ```bash
   adapt graph nexrad
   adapt run nexrad
   ```

What to know:

- The names under `params:` are the modules, the sources (`aws_nexrad`,
  `local_directory`, `stored_run`: the one `run.source` names is read), and
  three sections that belong to no module: `global` (the altitude of the slice,
  the field cells are tracked on, the largest gap between scans), `logging` and
  `queue` (how far the source may run ahead of the processing).
- `adapt run` and `adapt graph` check every option before anything starts. A
  misspelled option or a value out of its bounds is refused with its full name
  and the reason, for example
  `params.detection.h_maxima — value -1 is out of range — Input should be greater than 0`.
- Only the modules under `modules:` run. Options of a module the file does not
  list stay in the file unread, ready for when you list it.
- Every run records the options it ran with, defaults included
  (`client.run_config(run_id)`).
- **For one run, an option can be set on the command line** instead of in the
  file: `adapt run nexrad --set params.tracking.max_speed_ms=35`. The order is:
  the module's default, then the file, then the command line. See `adapt run`
  in the [CLI Reference](cli_reference.md).
- **A changed option makes a new run, not a new store.** But a table keeps the
  columns of the first run that wrote it in a collection. An option that adds
  columns, such as one more value in `cell_volume_stats.thresholds`, makes every
  scan of the new run fail with `new columns are rejected after first-frame
  freeze`. Use a new store folder for such a run.

The rules for values, for lists and for nested parts are under `adapt config`
in the [CLI Reference](cli_reference.md). The field names and the choice
of the tracked field are in [Variable Names](variable_names.md).

---

## Add or remove a module

The `modules:` list of the pipeline file is the whole pipeline. To add a
module, remove the `#` in front of its line; to remove one, put a `#` there.
The order of the lines does not matter: Adapt orders the modules by what each
reads and writes.

```yaml
modules:
  - ingest
  - detection
  - projection
  - analysis
  - tracking
  # - cell_volume_stats       # removed: no volume statistics in this run
```

A module cannot run without the modules it reads from. `adapt graph NAME`
prints who feeds whom, and both `adapt graph` and `adapt run` refuse a file
with a missing one, naming it:

```
Module 'tracking' needs input 'cell_stats', which module 'analysis' produces — list 'analysis' too, or remove 'tracking'
```

A module added to the file applies to new runs. To add a module to a run you
already have, see *Run a module later over a stored run* below.

### The modules

| Module | What it does | Reads from | What it stores |
|--------|--------------|------------|----------------|
| `ingest` | Reads one NEXRAD Level-II volume and regrids it to a Cartesian grid around the radar (default: 301 × 301 × 41 points, 1 km horizontal and 0.5 km vertical spacing, ±150 km, 0 to 20 km). Takes the horizontal slice at `global.z_level` (2000 m). | the scan file | the gridded volume, a NetCDF file per scan (`gridded3d`) |
| `detection` | Labels the cells in the slice. The default method, `threshold`, takes the area where the tracked field is above 35 dBZ and divides it into cells around the reflectivity maxima; cells under 5 grid points are dropped. Other methods use convective/stratiform classifications. | `ingest`; the previous scan's `projection` unless `seed_carry_frames: 0` | its decision log |
| `projection` | Computes the motion between the previous scan and this one by optical flow, and moves the cell labels forward: 3 scan steps ahead, and minute by minute for 15 minutes. | `detection` | nothing itself; its fields are in the file `tracking` stores |
| `analysis` | Measures each cell in the slice: area, area above 40 dBZ, centroids, mean, minimum and maximum of each radar variable, heading, and which cells touch. | `projection` | tables `cell_stats`, `cell_adjacency` |
| `tracking` | Links the cells of this scan to those of the previous one and gives each a `cell_uid`. Records how each track begins and ends, and every split and merge. | `projection`, `analysis` | the slice with labels, motion and projections, a NetCDF file per scan (`segmentation2d`); tables `cell_identity`, `cell_events`, `cell_tracks`; the view `cells_by_scan`; its decision log |
| `cell_volume_stats` | Measures each cell in the full volume: volume, base and top, echo-top heights at 10 to 50 dBZ, layering, polarimetric statistics. | `ingest`, `detection` | table `cell_volume_stats` |
| `target_selection` | Chooses, at each scan, the cell a radar should follow, with a rule you can edit. | `tracking`, and the cells and tracks stored so far | table `target_selection` |
| `arm_radar_comm` | Turns each choice into a radar command: a box around the projected cell, and a scan pattern. Records it; sends it only when you configure that. | `target_selection`, `tracking`, `cell_volume_stats` | table `radar_commands` |
| `xlma_stat` | Counts and describes the lightning flashes of each cell, minute by minute and summed up per scan, from Lightning Mapping Array files you provide. | `tracking` | tables `xlma_stat_minutes`, `xlma_stat_scan` (its `xlma_...` columns are in `cells_by_scan`) |

The first six are on in `nexrad.yaml`; the last three are there as commented
lines.

---

## Target selection and radar commands

Uncomment two lines of `nexrad.yaml` to run the whole chain: read scans, track
cells, choose a target, plan the radar command.

```yaml
modules:
  - ingest
  - detection
  - projection
  - analysis
  - tracking
  - cell_volume_stats
  - target_selection
  - arm_radar_comm
```

`adapt run nexrad` then also prints one line per scan for the command:

```
arm_radar_comm: no target
arm_radar_comm: command 8cbd87d5dbebeeab written for cell KD2WR93G0Q (dry run, nothing sent)
```

| Table | One row per | Holds |
|-------|-------------|-------|
| `target_selection` | scan with tracked cells | the chosen `cell_uid` (empty when no cell qualifies), the reason (`NEW_TARGET`, `CONTINUATION`, `SWITCH`), the score, and the candidates |
| `radar_commands` | decision | the outcome (`NO_TARGET`, `PUBLISHED`, ...) and the command itself (centre, extents, scan pattern) |

`adapt config --params target_selection arm_radar_comm` prints the options of
both, with the reasoning in comments.

- `target_selection` runs with a packaged rule. Change a part by giving only
  that part under `params:`:

  ```yaml
  params:
    target_selection:
      duration: {max_minutes: 15}     # the packaged rule with a 15-minute cap
  ```

- `arm_radar_comm` has **no defaults**: a file that lists it must give every
  one of its options, or the run is refused naming each one missing. Write them
  all into the file, from the packaged dry-run example, with:

  ```bash
  adapt config --params --file nexrad.yaml arm_radar_comm
  ```

  The example names `ARM_ADAPT_USER`, the environment variable that holds the
  user id sent with every command. Export it before `adapt run`, dry run
  included: `export ARM_ADAPT_USER=...`.

### Sending commands to a radar

**Nothing is sent to a radar unless you ask for it.** `api: null` is a dry
run: commands are recorded in the table only, with outcome `PUBLISHED`.

To send them, replace `api: null` with the `api:` block the example shows under
it. The block holds no address, key or path. It names three environment
variables, and the environment is the only source of what they hold. The base
URL is the radar's own (it ends with the radar, such as `/bnf`); commands are
POSTed to `{base URL}/publish/command`. Export them before `adapt run`:

```bash
export ARM_RADAR_URL=https://apigw.vsn.arm.gov/bnf   # api.base_url_env: the radar's API base URL
export ARM_API_CERT=/path/to/arm-ca.cer           # api.ca_file_env: CA certificate (PEM) the API's certificate is checked against
read -rs BNF_RADAR_API_KEY && export BNF_RADAR_API_KEY   # api.api_key_env: type or paste the key; it is not shown or saved in history
export ARM_ADAPT_USER=...                         # radar.user_id_env: the user id sent with every command
```

These are the names the example uses; the file may name others. A run whose
file has `api:` refuses to start when one of them is unset, the URL is not
`https://`, or the CA file cannot be read. Each command is one request
of the form `{"AutoTarget": {...}}`, never repeated. Its console line starts
with two marks: did the command go out, and did the API acknowledge it.

| Marks | Outcome | Meaning |
|-------|---------|---------|
| `✓✓` | `SENT` | sent, and the API answered HTTP 200 with `status: published` on queue `scan_commands` and the submitted target back |
| `✓✗` | `NOT_ACKNOWLEDGED` | sent, but the API answered an error, something other than that acknowledgment, or nothing readable |
| `✗–` | `SEND_FAILED` | not sent: the API could not be reached, or its settings were lost |

The reason follows the marks; the whole of it is in the table's `detail`
column. `SENT` means the command is in the radar's queue, not that the radar
ran it.

Only a fresh scan is sent. `api.max_scan_age_seconds` is how long ago, by this
machine's clock, a scan may have started when its command is ready to go out.
An older scan's command is recorded with outcome `STALE` and held back. This
keeps the backlog a run starts with, and every scan of a historical run, away
from the radar.

A scan's time is the start of its volume, and its file exists only when the
volume ends, 4 to 7 minutes later. So the limit has to cover one volume, the
time the source takes to notice the new file (up to about 30 s with the
`aws_nexrad` defaults) and about 30 s of processing. A limit below one volume
sends nothing; one that covers the volume but not the rest sends only some
scans. A limit of 480 covers both radars measured so far (commands ready 300 to
410 s after the scan's start).

---

## Run a module later over a stored run

A run does not have to be made in one go. A pipeline file with
`run.source: stored_run` runs its modules over a run the store already has. No
scan is read or gridded again: each module reads what it needs from what the
run stored. Use it to try another target selection rule, or to add lightning
statistics to a finished run.

1. Write a second pipeline file in the store, for example `rules_b.yaml`.

   ```yaml
   run:
     source: stored_run
     radar: KHTX
     mode: historical

   modules:
     - target_selection
     - arm_radar_comm

   params:
     target_selection:           # only the parts that differ from the packaged rule
       entry:
         gates:
           - {field: radar_reflectivity_max, op: ge, value: 50}
       switch: {allow: true, margin: 2.0}
   ```

2. Run it, naming the run to run over.

   ```bash
   adapt run rules_b --run-id 2026NOV18-2214-KHTX
   ```

The new rows are stored under the same `run_id`. For `target_selection` and
`arm_radar_comm` they sit beside the rows of the first rule: every row carries
`config_hash`, a short code of the options that made it, so decisions under
different rules stay apart.

What can run alone this way: a module whose inputs are stored. Today that is
`target_selection`, `arm_radar_comm` and `xlma_stat`. A module that reads the
grids from memory needs the modules that make them listed with it, and the
message says which. For example, a run made without `cell_volume_stats` gets
its volume statistics from this file:

```yaml
# addvol.yaml: volume statistics for a run made without them
run:
  source: stored_run
  radar: KHTX
  mode: historical

modules:
  - ingest
  - detection
  - projection                # detection reads the previous scan's projection
  - cell_volume_stats

params:
```

The scans are gridded again from the stored input files, so this pass takes as
long as the run did. The modules listed again must have the options the run
recorded for them, or the pass is refused.

The new columns need no change anywhere to be read: `cells_by_scan` carries
the columns of every table with one row per run, scan and cell label, whichever
module wrote it and whenever. After the pass above, the view has the volume
columns for that run. See *The per-cell view* in [Results](outputs.md).

The conditions (the run must have ended, the options a listed module ran with
before, what is never sent) are under *Running modules over a stored run* in
the [CLI Reference](cli_reference.md).

### Lightning statistics

`xlma_stat` attributes the flashes of a Lightning Mapping Array to the cells.
It reads xLMA *flash-sorted* NetCDF files (`*.nc`) from a folder you name; raw
LYLOUT text files must be converted with xLMA or pyxlma first. Each flash is
matched to the cell outline of its own minute, taken from the minute-by-minute
outlines that `projection` computes between two scans. It stores two tables:

- `xlma_stat_minutes`: one row per cell (`cell_uid`) and minute with lightning,
  plus one row per minute under `cell_uid` = `UNATTRIBUTED` for the flashes no
  cell was near. Every flash within the run's minutes is counted here.
- `xlma_stat_scan`: the minutes before a scan summed up, one row per cell of
  that scan with lightning. It has the key of the other per-cell tables (run,
  scan, cell label), so its columns (`xlma_flash_count`, `xlma_n_minutes`,
  ...) are in `cells_by_scan` beside the cell's uid and statistics. A cell
  that is no longer in the scan (its track ended or merged into another) has
  no row here; its lightning is in the minute table.

A collection where an earlier version of `xlma_stat` ran keeps the old
`xlma_stat_scan` table (key: run, scan time, cell uid; columns without
`xlma_`). There `xlma_stat` fails on every scan with `column 'cell_label' is
not in the frozen schema`: run lightning in a new store.

Enable it in a new run by uncommenting its line and giving the folder:

```yaml
modules:
  - ...
  - xlma_stat

params:
  xlma_stat:
    input_dir: /data/lma      # folder of xLMA flash-sorted NetCDF files
```

Or add it to a finished run with a file of its own, run with
`adapt run lightning --run-id <run id>`:

```yaml
# lightning.yaml
run:
  source: stored_run
  radar: KHTX
  mode: historical

modules:
  - xlma_stat

params:
  xlma_stat:
    input_dir: /data/lma
```

---

## Stop and resume a run

Press `Ctrl-C` to stop a run. The scan in progress is finished and saved, no
new scan starts, and the run is recorded as `cancelled`.

To go on, give the run's id:

```bash
adapt run nexrad --run-id 2026NOV18-2300-KHTX
```

The run continues with the scan after its last complete one. It works the same
after a crash or a reboot, and the result is the same as that of a run that
never stopped. A resumed run keeps the modules and options it was started
with; edits made to the file since are not applied.

The run id is on the first line a run prints. To find it later:

```bash
sqlite3 registry.db "SELECT run_id, status, started_at FROM runs"
```

or `client.runs()` in Python. The rules (which scans are processed again,
`--skip-failed`) are under *Resuming a run* in the
[CLI Reference](cli_reference.md).

---

## Several pipelines and runs in one store

- A store can hold several pipeline files. Copy `nexrad.yaml` to, say,
  `kdix.yaml`, edit it, and run `adapt run kdix`.
- Every `adapt run` without `--run-id` makes a new run. Runs of the same radar
  share one collection and its tables; filter by `run_id` when you read.
- The raw file of a scan is stored once, whatever the number of runs that
  process it.
- A new run's id is the minute it starts, so a second run started in the same
  minute is refused: wait a minute, or name it with
  `--run-id YYYYMONDD-HHMM-RADAR`.
- Only one `adapt run` can be active at a time for a user, across all stores.

---

## Dashboard

```bash
adapt dashboard
```

The dashboard is a desktop window that reads a store and never changes what a
run produced. Run it from the store folder, in a second terminal while a
pipeline runs or after it has ended. Started elsewhere, it opens the store you
used last; `adapt dashboard --repo PATH` opens the one you name.

It has three tabs:

| Tab | Shows |
|-----|-------|
| **Latest Scan** | One scan as a map: the chosen variable, the cell outlines, the projected positions. Click a cell to plot its history in three time-series panels. |
| **Target Selection** | A replay of a run under target selection rules you set in the tab. |
| **Log** | The output of a pipeline started from the dashboard. |

| Control | Does |
|---------|------|
| **Collection**, **Run** | choose the radar and the run to show |
| **Show Latest** (Space) | jump to the newest scan |
| **◄ / ►** (arrow keys) | one scan back or forward |
| **Show Loop** | animate the last N scans |
| **Variable**, **Min**, **Max** | the field shown (reflectivity, differential reflectivity, velocity, spectrum width) and its colour range |

A background map is drawn when `contextily` is installed
(`pip install "arm-adapt[maps]"`); it needs internet access for the map tiles.
The [Dashboard Reference](dashboard_reference.md) has the full list of
controls and the wizard that starts a pipeline from the window.

---

## Troubleshooting

| Message or symptom | What to do |
|--------------------|------------|
| `adapt: command not found` | Activate the environment Adapt is installed in (`conda activate adapt_env`). |
| `No Adapt store at ...: registry.db not found. Run 'adapt init' first.` | You are not in a store folder. `cd` into it, or run `adapt init`. |
| `Store already initialized at ...` | The folder is a store already. Run a pipeline file, or write one with `adapt config --nexrad`. |
| `No pipeline file .../NAME.yaml` | The file is not in the current folder. The message says how to make it. |
| `adapt run takes the name of a pipeline file ... not a path` | Give `nexrad` or `nexrad.yaml`, from inside the store folder. |
| `start_time is required when mode='historical'` | Set `run.start_time` and `run.end_time`. |
| `run.source_dir ... holds N file(s) that are not Level-II volumes of radar ...` | Move the other files out of the folder; it must hold only the radar's volumes. |
| `no scan in run.source_dir ... lies within run.start_time ..` | The window and the files do not overlap; the message gives the span of the files. |
| `A pipeline is already running (PID ...)` | Stop the other `adapt run`. If none is running, delete the file the message names. |
| `A run with id ... was started this minute` | Wait a minute, or name the run with `--run-id`. |
| `Scan failed` with `new columns are rejected after first-frame freeze` | The options of this run add table columns to an existing collection. Use a new store folder. |
| `... uses the obsolete pre-store layout (adapt_registry.db)` | The folder holds output of an early version of Adapt. Move it aside, run `adapt init`, and process again. |
| A scan failed | The reason is in the run's log, `logs/pipeline_<RADAR>.log`. `adapt run NAME -v` prints debug messages on the console too. |
| The first scan shows `0 cells` | Expected: tracking starts with the second scan (see *First run*). |
| The dashboard shows no data just after a run starts | Tracked cells exist from the second scan on. Wait for it, then click **Show Latest**. |
| The background map does not load | `contextily` is missing or there is no internet access. |

---

## Where to go next

| To | Read |
|----|------|
| Look up a command, a flag or a rule of the pipeline file | [CLI Reference](cli_reference.md) |
| Look up a table, a column or a file | [Results](outputs.md) |
| Use another data source or track another field | [Variable Names](variable_names.md) |
| Use every control of the dashboard | [Dashboard Reference](dashboard_reference.md) |
| Write your own module | [Module Developer Guide](module-developer-guide.md) |
| Call the store from Python | [StoreClient API](api/client.rst) |
