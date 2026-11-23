# CLI Reference

This page is the complete description of the `adapt` command and of the
pipeline file. It is for someone who has made a first run with the
[User Guide](USAGE.md) and now needs the exact rule: what a flag does, what a
section of the file accepts, what is checked and when. The words *store*,
*run*, *scan* and *module* are explained in the User Guide.

```
adapt init                           # create a store and its pipeline file (nexrad.yaml)
adapt config --nexrad [--params]     # write ./nexrad.yaml from the template, never over a file
adapt config --params NAME...        # print NAMEs' options with their defaults, ready to paste
adapt config --params --file F [NAME...]  # add to F the options its modules lack
adapt run NAME                       # run the pipeline file ./NAME.yaml
adapt graph NAME           # print the module graph of ./NAME.yaml (Mermaid text)
adapt dashboard            # open the GUI dashboard
```

The project folder **is** the store. A pipeline is the file `NAME.yaml` in that
folder, and `adapt run NAME` reads `./NAME.yaml` from the current directory,
which must be a store created by `adapt init`. There are no paths to pass.
`adapt run` and `adapt graph` take the file with or without its extension:
`adapt run nexrad.yaml` is `adapt run nexrad`.

`adapt --version` prints the installed version and the path Adapt was installed
to. `adapt --help` and `adapt <command> --help` describe each command.

| Exit code | Meaning |
|-----------|---------|
| `0` | success |
| `1` | a run that was stopped or did not complete; a run in which a module failed on a scan; the folder is not a store, or is one already; another pipeline is running |
| `2` | an invalid pipeline file, option or argument; a `--run-id` that names no run the command can use |

---

## `adapt init`

Create the store layout (`registry.db`, `logs/`, `collections/`), then one
pipeline file per template: today `nexrad.yaml`. This is the only command that
creates a store. `adapt run` refuses a folder that is not one.

```bash
adapt init            # initialize the current directory
adapt init /data/kilx # initialize a specific directory
```

| Argument | Description |
|----------|-------------|
| `directory` | Root directory for the new store. Defaults to the current directory. Refused when the folder is a store already, is inside another store, or holds the output of a version of Adapt from before the store (`adapt_registry.db`). A pipeline file that already exists there is kept. |

---

## `adapt config`

```bash
adapt config --nexrad                         # write ./nexrad.yaml: the modules, an empty params:
adapt config --nexrad --params                # the same, with every module's options under params:
adapt config --params tracking projection     # print these modules' options to paste
adapt config --params global                  # print a section's options to paste
adapt config --params --file nexrad.yaml tracking projection   # add their missing options
adapt config --params --file nexrad.yaml      # ... of every module the file lists
```

| Argument | Description |
|----------|-------------|
| `--nexrad` | Write `./nexrad.yaml` from the `nexrad` pipeline template (one flag per template). Refused when the file exists: it is never overwritten. With `--params`, every module of the catalog, listed or not, gets all its options under `params:`. Takes no `NAME` and no `--file`. |
| `--params` | Print the options of each `NAME`, or with `--file` add them to the file. Needs `NAME`s or `--file`. |
| `--file FILE` | With `--params`: the pipeline file to add options to. |
| `NAME...` | Modules (to print, also the sources `aws_nexrad`, `local_directory`, `stored_run` and the sections `global`, `logging`, `queue`). An unknown name lists every valid one. |

### Printing options

`--params NAME...` accepts:

- a **module** (`ingest`, `detection`, `projection`, `analysis`, `tracking`,
  `cell_volume_stats`, `target_selection`, `arm_radar_comm`, `xlma_stat`): its
  options under its own name, each with its default and a one-line meaning,
  after a heading comment naming the module. `target_selection` prints its
  packaged rule (`growing_isolated`) with the reasoning in comments;
  `arm_radar_comm`, which has no defaults, prints every option it needs as a
  dry-run example to copy, with the environment variables it reads;
- a **source** (`aws_nexrad`, `local_directory`, `stored_run`): its options
  under its own name, after a heading comment with its summary; `stored_run`
  has none;
- `global` (the settings of the whole run, shared by several modules),
  `logging` or `queue`.

The first line of the output names the line to paste the rest under: the
file's `params:` line. The entries are indented to sit under that line, two
empty lines between them, and they load together. The template ends with
`params:`, so `adapt config --params NAME >> nexrad.yaml` pastes them there too.
An entry given twice is an error.

You rarely need the whole output: a module listed under `modules:` runs with
its defaults. Give only what you change (see *How options are read* below).

### Adding options to a file

`--params --file FILE NAME...` adds to `FILE` every option of the modules
`NAME` that it does not give, at its default; with no `NAME`, of every module
`FILE` lists under `modules:`. A `NAME` may be a module `FILE` does not list:
its options are kept in the file and used once the module is listed.

- A module that `params:` does not give is added at the end of `params:`, as
  `--params NAME` prints it: a heading comment, then its options, two empty
  lines before it.
- A module that `params:` gives keeps every value it gives, and gains the
  options missing from each part it gives, at the end of that part. One
  changed key deep inside a nested part (`projection.flow_params.levels`)
  stays, and the other keys of that part are added beside it. A list or a
  mapping of free keys (`target_selection.score.weights`) is replaced whole by
  what the file gives (*How options are read*), so nothing is added to it.
- Every other line of the file is kept as it is: values, comments, empty lines
  and the order. Adding a default changes no option the run uses.
- A second run adds nothing and leaves the file untouched; so does a run on a
  file written with `--nexrad --params`.

Refused, with the file left as it was: a `FILE` that does not exist, is not
valid YAML or is not a valid pipeline file; a `NAME` that is not a module
(a section included: print it and paste it); a module or part written on one
line (`tracking: {max_speed_ms: 35}`), which cannot take more lines: write it
one option per line first.

---

## The pipeline file

`adapt init` and `adapt config --nexrad` write this file (shown here without its
header comments). Only names are listed; every option not given under `params:`
uses its default. The file does not hold its own name, so a copy of it is a
valid pipeline file as it is.

```yaml
run:
  source: aws_nexrad          # where scans come from: aws_nexrad | local_directory | stored_run
  radar: KLOT                 # radar / collection id
  mode: realtime              # realtime | historical
  start_time: null            # historical: ISO 8601 UTC, e.g. 2025-06-17T18:00:00Z
  end_time: null
  source_dir: null            # local_directory: folder of Level-II files
  max_runtime_minutes: null   # realtime: stop after this many minutes; null = until Ctrl-C

modules:                      # per-scan modules; Adapt orders them by their inputs and outputs
  - ingest                    # read + regrid radar volumes
  - detection                 # segment cells from the grid
  - projection                # optical-flow projection between scans
  - analysis                  # 2D per-cell statistics
  - tracking                  # link cells across scans
  - cell_volume_stats         # 3D volume stats per cell (base, top, echo-top heights)
  # - target_selection        # choose the cell the radar should watch
  # - arm_radar_comm          # command the radar to the selected cell; needs target_selection
  # - xlma_stat               # Lightning (xLMA) statistics on minute-projected cells; needs params: xlma_stat: {input_dir: ...}

params:
```

All three sections must be present. One whose entries are all commented out is
empty.

### `run`

| Key | Values | Rules |
|-----|--------|-------|
| `source` | `aws_nexrad`, `local_directory`, `stored_run` | Where the scans come from: the public NEXRAD archive on AWS, a folder on disk, or a run the store already has. |
| `radar` | 4-letter site code | Also the name of the collection the results go to. |
| `mode` | `realtime`, `historical` | `local_directory` and `stored_run` need `historical`. |
| `start_time`, `end_time` | ISO 8601 with a time zone (`Z` = UTC), or `null` | For `mode: historical` only, where both are required; `end_time` must be after `start_time`. Not allowed with `stored_run`. |
| `source_dir` | a folder, or `null` | Required by, and only for, `source: local_directory`. Every file in it must be a Level-II volume of `radar`, named `RADARYYYYMMDD_HHMMSS...` (hidden files are ignored). Only the files within `start_time`..`end_time` are processed; a folder with no scan in the window is refused. |
| `max_runtime_minutes` | a whole number, or `null` | For `mode: realtime` only. `null` runs until `Ctrl-C`. |

A key that the mode or source would ignore is refused, never dropped.

### `modules`

The modules to run on every scan, in any order: Adapt orders them by what each
reads and writes. An unknown module is an error that lists the valid names. A
module whose input another, unlisted, module produces is refused, naming the
module to list.

There is one kind of module. Every listed module runs once per scan, as soon as
the modules it reads from are done, and its outputs are written to the store
when it finishes. What each module does and stores is listed under *The
modules* in the [User Guide](USAGE.md); the tables and files are described in
[Results](outputs.md).

### `params`

A mapping from a name to options. The valid names are the modules of the
catalog, the sources of the catalog, and three sections of the run:

| Name | Options of |
|------|-----------|
| a module (`ingest`, `detection`, `tracking`, ...) | that module |
| a source (`aws_nexrad`, `local_directory`, `stored_run`) | that source, when `run.source` names it: for `aws_nexrad` how far back a realtime run looks at its start, when it looks for each new volume, how many volumes it may skip while processing is behind, the fetch retries and the smallest file kept; for `local_directory` the smallest file kept; `stored_run` has none |
| `global` | the whole run, shared by several modules: `tracking_field`, `z_level`, `max_scan_gap_minutes` |
| `logging` | the log file and the console |
| `queue` | the queue between the source and the processor: how many scans the source may run ahead (`max_queue_size`) and when it resumes (`queue_resume_fraction`) |

Only the modules listed under `modules:` and the source named by `run.source`
run, so only their options are read and checked. Options of a module the file
does not list, or of a source it does not name, are kept as they are and not
read: listing the module, or naming the source, turns them on. Any other
unknown name is an error that lists the valid ones.

### How options are read

What you give under `params.<name>` is laid over the defaults, part by part:

```yaml
params:
  target_selection:
    duration: {max_minutes: 15}   # the packaged rule with a 15-minute cap
```

changes the cap and keeps every other part of the rule. This holds at any
depth: one key deep inside a nested part can be given alone
(`score: {site_preference: {projection_steps: 2}}`). The exceptions:

- `arm_radar_comm` has no defaults at all: every option is given, `api` and
  `extent.vertical_band` included (`null` for none). `adapt config --params
  --file FILE arm_radar_comm` writes them all.
- A list, and an option that is a mapping of free keys
  (`target_selection.score.weights`, `ingest.field_map`), is replaced whole.
- `null` is a value like any other, never "use the default": it is refused for
  an option that does not allow it.
- A key given twice in one mapping is an error that names both lines.

Rules about values:

- A number must be finite. `.inf` and `.nan` are refused for every module
  option and for `global.z_level` and `global.max_scan_gap_minutes`: the
  options are recorded with the run, and the record holds neither.
- Free text is trimmed of the spaces around it in the options of `ingest`,
  `detection`, `projection`, `analysis` and `tracking` and in
  `global.tracking_field`: `" velocity "` is `velocity`.
- An option with a fixed set of values takes one of them exactly as `--params`
  prints it: `projection.method` must be `adapt_default`; capital letters, or
  spaces around it, are refused.

### When the file is checked

`adapt run` and `adapt graph` check the whole file before anything starts, in
this order:

1. **The structure**: the three sections, the keys of `run`, the module names,
   the names under `params:`.
2. **Every option**: unknown options, wrong types, values out of bounds,
   non-finite numbers. All of them are reported together, one line per option,
   named by its full path with the reason, then one line per module with the
   command that prints its options:

   ```
   Invalid configuration (pipeline file /data/kilx/nexrad.yaml):
     • params.detection.h_maxima — value -1 is out of range — Input should be greater than 0
     • params.detection.h_maximum — unrecognized option (value: 3) — not a valid 'detection' setting; remove it or check for a typo or a renamed field
   The options of detection: adapt config --params detection
   ```

3. **What a module refuses itself** and the wiring, one at a time: the two
   rules on `global.tracking_field` (see [Variable Names](variable_names.md)),
   the `xlma_stat` folder, a module missing an input, a missing time window.

`adapt run` then also checks the source (for `local_directory`, the folder and
its files) and, when the file lists `arm_radar_comm`, the user id in the
variable `radar.user_id_env` names; when it also has an `api:` part, the base
URL (`https://`, the radar's own, such as `https://apigw.vsn.arm.gov/bnf`), the
key and a readable CA certificate file, each in the variable the part names
(`base_url_env`, `api_key_env`, `ca_file_env`). The environment is
their only source; the file never holds them.

---

## `adapt run`

```bash
adapt run nexrad                              # run ./nexrad.yaml
adapt run nexrad.yaml                         # the same
adapt run nexrad -v                           # with DEBUG logging
adapt run nexrad --run-id 2026OCT21-1052-KFSX # resume that run, or start one with this id
adapt run nexrad --run-id 2026OCT21-1052-KFSX --skip-failed
adapt run nexrad --set params.tracking.max_speed_ms=35   # one option, for this run
```

| Argument | Description |
|----------|-------------|
| `NAME` | The pipeline file `./NAME.yaml` in the current folder (a store), given as `NAME` or `NAME.yaml`. A path is refused, `./nexrad.yaml` included. |
| `--run-id ID` | The format is `YYYYMONDD-HHMM-RADAR`. An id of a run in the store resumes that run (see *Resuming a run*). An id the store does not have starts a new run with that id; it must end with `-` and the file's `run.radar`. With `run.source: stored_run`: the run to run over (required). |
| `--skip-failed` | Resuming `--run-id`: leave the scans that failed before as failed instead of processing them again. |
| `-v`, `--verbose` | DEBUG-level logging in the run's log and on the console. |
| `--set params.KEY.OPTION=VALUE` | Set one option for this run, over what the file says. May be given more than once. See *Setting an option on the command line*. |

Without `--run-id`, a new run's id is the minute it starts, in local time, and
the radar. A second run started within that minute is refused (wait a minute,
or name it with `--run-id`); it is never joined to the first.

### What a run does

The source provides scans in time order. For each scan the listed modules run
as one graph (`adapt graph NAME` prints it), and each module's outputs are
stored when it finishes.

- **First scan and gaps.** A module that needs the scan before (`projection`,
  and everything that reads from it) is skipped on the first scan of a run and
  after a gap longer than `global.max_scan_gap_minutes`. A run of N scans gives
  N−1 scans of tracked cells, decisions and commands.
- **Failures.** A module that fails skips the modules that read from it. The
  scan is recorded `failed` with the reason per module, and the run goes on
  with the next scan as if the failed one had not arrived. The command then
  exits `1`, with a summary that says so (`COMPLETED, 2 SCANS FAILED`).
- **The console.** One line per scan, with the time each module took. Warnings
  and errors are printed as they happen. Everything else goes to the log file
  `logs/pipeline_<RADAR>.log`. `arm_radar_comm` also prints one line per scan:
  the command it wrote or sent, or why there is none.
- **Stopping.** `Ctrl-C` (or SIGTERM) stops the run: no new scan starts, the
  scan in progress is finished and saved, and the run is recorded `cancelled`.
  A stop pressed while a scan is being read may take until that read ends, 10
  to 30 s on a full volume, to be acted on.
- **One at a time.** Only one pipeline runs at a time for a user (PID file
  `~/.adapt/pipeline.pid`; one left behind by a pipeline that has exited is
  ignored).

The outcomes `arm_radar_comm` records are: `NO_TARGET`, `TARGET_UNAVAILABLE`,
`VALIDATION_FAILED`, `DUPLICATE`, `PUBLISHED` (a dry run), and, only when
`api:` is set, `STALE` (the scan was older than `api.max_scan_age_seconds`:
held back), `SENT` (sent and acknowledged as queued, not as run by the radar),
`NOT_ACKNOWLEDGED` (sent but not acknowledged) and `SEND_FAILED` (not sent).
The command is stored as it is sent, `{"AutoTarget": {...}}`.

### Resuming a run

After every complete scan the run saves a checkpoint: what its modules carry
from scan to scan (the tracker's tracks, the current target, the commands
already published) and the last scans they read back. Only the latest is kept.
`adapt run NAME --run-id ID` goes on from it, after a `Ctrl-C`, a crash or a
kill alike, and the result is the same as if the run had never stopped:

- scans completed before the stop are not processed again;
- the scan that was in progress, or that failed last, is processed again
  (`--skip-failed` leaves a failed one as it is);
- a scan that failed earlier, with complete scans after it, stays failed: its
  turn has passed.

The resumed run keeps the modules and options it was started with: it is
configured from the options the run recorded (every listed module's options,
defaults included), not from the file. Of the file only
`run.max_runtime_minutes` applies, and `run.radar` must be the run's. The
file's structure is still checked, but its options are not read, so a wrong
option goes unreported there (a new run of the file, and `adapt graph`, report
it). A run whose checkpoint does not hold the state its modules need is
refused, naming them.

A run whose recorded options this version of Adapt cannot read cannot be
resumed or used for a stored-run pass: the message names the run and the keys
it cannot read. Start a new run instead.

### Running modules over a stored run (`run.source: stored_run`)

With `source: stored_run` no scan is downloaded: the listed modules run over
every scan of the run `--run-id`, in scan order. Each input that no listed
module produces is read back from the store when it is a stored output (a
module's table or NetCDF file). A module that needs an input the store does not
hold (the grids and labels that modules pass each other in memory:
`segmented_ds`, `projected_ds`, `grid_ds_2d`) is refused, naming the module to
list with it. This is how a module is added to a run after the fact, or how
target selection is run again with another rule:

```yaml
# rules_b.yaml: another target selection rule over a stored run
run:
  source: stored_run
  radar: KFSX
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

```bash
adapt run rules_b --run-id 2026OCT21-1052-KFSX
```

```yaml
# lightning.yaml: add xLMA lightning statistics to a stored run
run:
  source: stored_run
  radar: KLOT
  mode: historical

modules:
  - xlma_stat

params:
  xlma_stat:
    input_dir: /data/lma      # folder of xLMA flash-sorted NetCDF files
```

The rules:

- The run must be of `run.radar` and must have ended (a run still `running` is
  refused; an unknown id lists the store's runs of the radar).
- A listed module is configured as in a new run: its own defaults, then the
  file's `params.<module>`. The options the stored run recorded for it are not
  carried over.
- The run keeps the shared settings it was made with: `params.global`,
  `params.logging` and `params.queue` are refused. `stored_run` has no
  options, so `params.stored_run` with any option is refused too.
- A listed module always runs, replacing what it stored before under the same
  key. So if the stored run itself ran a listed module with other options, the
  pass is refused: rows of both sets of options would be left mixed. The
  message names each option that differs, with the recorded value and the
  file's. Give the module the recorded options, or start a new run with the
  new ones.
- `target_selection` and `arm_radar_comm` are exempt from that refusal: their
  rows are keyed by the hash of the options that made them (`config_hash`), so
  decisions under different rules sit side by side in the same tables.
- A module whose input is not in the store for a scan is skipped for that scan,
  with the reason recorded.
- `arm_radar_comm` never sends over a stored run, with or without `api:`.
- The run's checkpoint is left as it is.
- A table with one row per run, scan and cell label that the pass stores is
  read through the view `cells_by_scan` from then on (see
  [Results](outputs.md)).

### Setting an option on the command line

`--set` gives one option for one run without editing the file. An option is
read in this order: the module's default, then the pipeline file, then the
command line.

```bash
adapt run nexrad --set params.tracking.max_speed_ms=35
adapt run nexrad --set params.global.z_level=3000 --set params.detection.threshold_params.threshold=40
adapt graph nexrad --set params.detection.h_maxima=4     # check it first, nothing runs
```

- The path is the option's full name as `adapt config --params` and the error
  messages give it: `params`, then a module, a source or one of the sections
  `global`, `logging`, `queue`, then the option. A key deep inside a nested part is
  set alone, like in the file. Only `params` can be set; `run:` and
  `modules:` are the file's.
- The value is read as the file would read it: `35`, `3.5`, `true`, `null`,
  `[velocity, spectrum_width]`, `{reflectivity: DBZ}`, or text. Quote it for
  the shell when it has spaces or brackets.
- The setting is checked like the file's options, and a wrong one is refused
  before anything starts. The message names the file and the settings:
  `Invalid configuration (pipeline file /data/khtx/nexrad.yaml with --set params.tracking.max_speed_ms=0):`.
  One refusal still says "this file": a pass over a stored run with other
  options than the run recorded for a module.
- The file must have its `params:` section, even an empty one; `--set` does
  not supply it.
- Of two settings of the same option, the later one holds.
- A new run records what was set (`client.run_config(run_id)`:
  `pipeline.content.overrides`, in the order given) and the options it ran
  with under `module_params`. `pipeline.content.params` holds the file's
  params with the settings laid over them. A pass over a stored run records
  neither its file nor its `--set` settings: the stored run's record stays as
  it was.
- A resumed run keeps the options it recorded, so `--set` with the `--run-id`
  of a run in the store is refused. Over a stored run (`run.source:
  stored_run`) it sets options of the listed modules, like the file.

---

## `adapt graph`

Print the module graph of the pipeline file `./NAME.yaml` as
[Mermaid](https://mermaid.js.org) text. Nothing runs, and the folder does not
have to be a store.

```bash
adapt graph nexrad                # print the graph
adapt graph nexrad > nexrad.mmd   # save it; open it in any Mermaid viewer
adapt graph nexrad --set params.detection.h_maxima=4   # with one option set, as adapt run takes it
```

The graph is the one a new run of the file would build, from the modules'
declared inputs and outputs and the file's options:

- `modules`: the listed modules. Each arrow is labelled with the data one
  module hands the next, e.g. `ingest -->|grid_ds_2d, grid_ds| detection`.
- `scan` and `prior_scans`: what Adapt provides to every scan. `scan` is the
  file, its identity and the machine's time as the module that reads it starts
  (`wall_time`, which `arm_radar_comm` compares with the scan's time before it
  sends). `prior_scans` is what modules read back from the scans before. A
  module that needs a prior scan is skipped on the first scan and after a
  too-long gap, and so is every module downstream of it.
- `store`: what is read from the store: the cells and tracks so far (for
  `target_selection`) and, over a stored run, each stored output that no
  listed module produces.
- Dotted arrows (`-.->`): a module that reads stored rows back runs after
  every listed module that writes them; the label is the table or view read,
  e.g. `cell_volume_stats -.->|cells_by_scan| target_selection`.

An option can change what a module reads: with
`arm_radar_comm.extent.vertical_band` set, `arm_radar_comm` no longer reads
`cell_volume_stats`, and the arrow is gone. A file a run would refuse (an
unknown module, a wrong option, a module listed without the one that produces
its input) is refused here with the same message, exit code `2`.

---

## `adapt dashboard`

Launch the GUI dashboard.

```bash
adapt dashboard
adapt dashboard --repo ~/adapt_output
```

| Flag | Description |
|------|-------------|
| `--repo PATH` | The store to open. Without it the dashboard opens the current folder when it is a store, and otherwise the store used last; when there is neither, it asks. |

The dashboard reads from an existing store and does not affect a running
pipeline; run it in a second terminal while the pipeline runs. Its **Start New
Pipeline…** wizard writes `dashboard.yaml` in the chosen store and runs
`adapt run dashboard` there (see [Dashboard Reference](dashboard_reference.md)).

---

## Pipeline files of earlier versions

This section is for someone who has a pipeline file written for an earlier
version of Adapt. A new user can skip it.

A file with the sections of earlier versions (`core:`, `extended:`,
`consumers:`, `postprocessing:`) is refused: write a new one with
`adapt config --nexrad` and list the modules under `modules:`.

Earlier versions kept the options of five modules under other names. Those
names are now unknown, both under `params:` and for `adapt config --params`, and
are refused with the list of valid ones. Move the options under the module that
uses them:

| Old key under `params:` | Now |
|---|---|
| `reader`, `regridder` | `ingest` |
| `segmenter` | `detection` |
| `projector` | `projection` |
| `analyzer` | `analysis` |
| `tracker` | `tracking` |
| `tracker.cell_uid.width` | `tracking.uid_width` |
| `tracker.cell_uid.alphabet` | removed (it had no effect); refused as an unknown option, like any other |
| `visualization`, `output` | removed (they had no effect on a run) |
| `global.coord_names` | removed: the grid coordinate names are fixed (`x`, `y`, `z`, `time`) |
| `analyzer.exclude_fields` (defaults plus your entries) | `analysis.exclude_fields` lists only your additions. Five internal variables are always excluded (`ROI`, `labels`, `cell_labels`, `cell_projections`, `clutter_filter_power_removed`); `exclude_fields: []` no longer clears them |

The option names inside a section are otherwise unchanged
(`segmenter.h_maxima` is `detection.h_maxima`). `global` and `logging` keep
their names.

The section `downloader` is gone. A file that sets `params.downloader` is
refused, and the message names the new place of each option it sets. Move them:

| Old option under `params.downloader` | Now |
|---|---|
| `latest_files`, `latest_minutes`, `max_fetch_retries` | `aws_nexrad.<same name>` |
| `poll_interval_sec` | no longer an option: `aws_nexrad.first_check_sec`, `expected_arrival_sec` and `late_after_sec` set when a realtime run looks |
| `min_file_size` | `aws_nexrad.min_file_size` or `local_directory.min_file_size`: the source `run.source` names |
| `max_queue_size`, `queue_resume_fraction` | `queue.<same name>` |
| `output_dir` | removed (it had no effect) |

A run recorded before this change cannot be resumed (`--run-id`) or passed
over (`run.source: stored_run`); both are refused before anything starts, with
the keys this version cannot read and "start a new run". Its store still opens
and reads as before. A value that was refused before (out of its
bounds, of the wrong type, `null` where the option does not allow it) is
refused as before, under the new key.

What else is different from earlier versions:

- **Options of a module the file does not list** are kept and not read
  (`params.tracking` in a file whose `modules:` has no `tracking`), as earlier
  versions did with the old sections. Only a name that is no module, source
  or section is refused.
- **The two rules on `global.tracking_field`** (it must be in
  `analysis.radar_variables`, and in `ingest.fields` when that list is not
  empty) are each checked by the module they concern, so they apply only in a
  file that lists `analysis`, respectively `ingest`. Earlier versions applied
  them to every file.
- **On a resume** (`--run-id` of a run in the store) the file's options are not
  read, so a wrong one is not reported. Earlier versions refused the file.
- **`.inf` and `.nan`** are refused for every module option and for
  `global.z_level` and `global.max_scan_gap_minutes`. Earlier versions
  accepted them wherever the option's bounds did. Give a finite number, or
  `null` where the option allows it (for `target_selection`,
  `duration: {max_minutes: null}` is no cap).
- **`projection.method`** must be written exactly `adapt_default`. Earlier
  versions also took other letter case and surrounding spaces.
- **A run recorded with the old keys** cannot be resumed or used for a
  stored-run pass; start a new run.
