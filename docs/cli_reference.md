# CLI Reference

Adapt command structure: `adapt <command> [NAME] [options]`

```
adapt init                 # create a data store and its pipeline files (nexrad.yaml)
adapt config NAME          # (re)write the pipeline file ./NAME.yaml from template NAME
adapt config --show NAME   # print NAME's options with their defaults, ready to paste
adapt run NAME             # run the pipeline file ./NAME.yaml
adapt graph NAME           # print the module graph of ./NAME.yaml (Mermaid text)
adapt dashboard            # open the GUI dashboard
```

The project folder **is** the store. A pipeline is the file `NAME.yaml` in that
folder, and `adapt run NAME` reads `./NAME.yaml` from the current directory,
which must be a store created by `adapt init`. There are no paths to pass.
`adapt run` and `adapt graph` take the file with or without its extension:
`adapt run nexrad.yaml` is `adapt run nexrad`.

`adapt --version` prints the installed version and the path Adapt was installed
to. `adapt --help` and `adapt <command> --help` describe each command from the
command line.

Exit codes: `0` success; `1` a run that did not complete or was stopped, a run
in which a scan failed, or a store error (not a store, already a store, unknown
run); `2` an invalid pipeline file or option.

---

## `adapt init`

Create the store layout (`registry.db`, `logs/`, `collections/`), then one
pipeline file per template — today `nexrad.yaml`. This is the only command that
ever creates a store; `adapt run` and `adapt dashboard` fail loudly on an
uninitialized directory.

```bash
adapt init            # initialize the current directory
adapt init /data/kilx # initialize a specific directory
```

| Argument | Description |
|----------|-------------|
| `directory` | Root directory for the new store. Defaults to the current directory. Fails if the store already exists or the directory uses the obsolete pre-store layout. A pipeline file that already exists there is kept. |

---

## `adapt config`

```bash
adapt config nexrad                   # (re)write ./nexrad.yaml; asks before overwriting
adapt config --show segmenter         # print options to paste into a pipeline file
adapt config --show target_selection  # a module's packaged options, with their comments
```

| Argument | Description |
|----------|-------------|
| `NAME` | Pipeline template to write as `./NAME.yaml`. Available: `nexrad`. An unknown name lists the available templates. |
| `--show NAME` | Print NAME's options as commented lines to paste under the pipeline file's `params:` line, each with a one-line meaning. |

`--show` accepts:

- a **module**: `ingest`, `detection`, `projection`, `analysis`, `tracking`
  print the `params:` sections that hold their options (ingest → `reader`,
  `regridder`; detection → `segmenter`; projection → `projector`; analysis →
  `analyzer`; tracking → `tracker`); `cell_volume_stats` and `xlma_stat` print
  their own `NAME:` entry; `target_selection` and `radar_command` print their
  packaged options with the reasoning in comments (the `growing_isolated` rule
  and a dry-run `sandbox` command setup);
- a **params section**: `reader`, `downloader`, `regridder`, `segmenter`,
  `global` (settings shared by several modules), `projector`, `analyzer`,
  `tracker`, `visualization`, `logging` (`output` is accepted but says it has no
  options left: the ones it had are obsolete).

An unknown name lists every valid one. The first line of the output names the
line to paste the rest under: the file's `params:` line. Paste it there as
printed — the entries are indented to sit under that line, and fragments pasted
one after another load together. The template ends with `params:`, so
`adapt config --show NAME >> nexrad.yaml` pastes them there too. You rarely need
the whole fragment: a module listed under `modules:` runs with its defaults,
which for `target_selection` and `radar_command` are the packaged options
`--show` prints. Give only what you change; it is laid over the defaults part
by part, so

```yaml
modules:
  - ...
  - target_selection
  - radar_command

params:
  target_selection:
    duration: {max_minutes: 15}   # the packaged rule with a 15-minute cap
```

changes the cap and keeps every other part of the rule. A misspelled option is
refused, naming it. An entry given twice is an error, never silently
half-dropped.

---

## The pipeline file

`adapt init` and `adapt config nexrad` write this short form (shown here
without its header comments). Only names are listed; every module option not
given under `params:` uses its default. The file names no pipeline, so a copy
of it is right as is.

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
  # - radar_command           # command the radar to the selected cell; needs target_selection
  # - xlma_stat               # Lightning (xLMA) statistics; needs params: xlma_stat: {input_dir: ...}

params:
```

| Section | Holds |
|---------|-------|
| `run` | Where scans come from (`source`, `radar`, `mode`, `start_time`, `end_time`, `source_dir`) and `max_runtime_minutes`. Times need a time zone (`Z` = UTC) and are for `mode: historical` only, with `end_time` after `start_time`; `max_runtime_minutes` is for `mode: realtime` only; `source_dir` is required by, and only for, `source: local_directory`, which needs `mode: historical`: the folder's Level-II files must be named for `run.radar` (`RADARYYYYMMDD_HHMMSS...`; hidden files are ignored), and only those within `start_time`..`end_time` are processed — a folder with no scan in the window is refused. `source: stored_run` runs the modules over a run the store already has (below); it needs `mode: historical` and no time window. |
| `modules` | The modules to run on every scan, in any order: Adapt orders them by what each reads and writes. A module whose input another, unlisted, module produces is refused, naming the module to list. |
| `params` | A params section name (`segmenter`, `tracker`, `global`, …), or the name of a listed module that owns its options (`cell_volume_stats`, `target_selection`, `radar_command`, `xlma_stat`) → options. |

There is one kind of module. Every listed module runs once per scan, as soon as
the modules it reads from are done, and its outputs are written to the store
when it finishes. `target_selection` and `radar_command` are modules like the
others: their decisions and commands are store tables (`target_selection`,
`radar_commands`), not files.

Every section must be present; one whose entries are all commented out is
empty. An unknown module, an unknown key anywhere, a key given twice, or the
name of a module whose options live in params sections (`ingest`, `detection`,
…) under `params:` is an error that names the valid choices. A file with the
sections of earlier versions (`core:`, `extended:`, `consumers:`,
`postprocessing:`) is refused: write a new one with `adapt config nexrad` and
list the modules under `modules:`. Each params section is checked against its
schema: a value out of its bounds is refused, and so is an obsolete option.
Params of a module the file does not list are refused: list the module or
remove them. `params.downloader` cannot set `radar`, `mode`, `start_time` or
`end_time` — those belong to `run:`.

A store may hold several pipeline files: copy `nexrad.yaml` to `kdix.yaml`,
edit it, and run `adapt run kdix`.

---

## `adapt run`

```bash
adapt run nexrad                              # run ./nexrad.yaml
adapt run nexrad.yaml                         # the same
adapt run nexrad -v                           # with DEBUG logging
adapt run nexrad --run-id 2026OCT21-1052-KFSX # resume that run, or start one with this id
adapt run nexrad --run-id 2026OCT21-1052-KFSX --skip-failed
```

| Argument | Description |
|----------|-------------|
| `NAME` | The pipeline file `./NAME.yaml` in the current folder (a store), given as `NAME` or `NAME.yaml`. A path is refused, `./nexrad.yaml` included. |
| `--run-id ID` | A run already in the store is resumed from its last checkpoint with its recorded modules and params — of the file only `run.max_runtime_minutes` applies, and `run.radar` must be the run's. An unknown id (format `YYYYMONDD-HHMM-RADAR`, ending with `-` and the file's `run.radar`) starts a new run with that id. Without `--run-id` a new run's id is the minute it starts; a second run started within that minute is refused (wait a minute, or name it with `--run-id`), never joined to the first. With `run.source: stored_run`: the run to run over (required). |
| `--skip-failed` | Resuming `--run-id`: leave the scans that failed before as failed instead of processing them again. |
| `-v`, `--verbose` | DEBUG-level logging in the run's log and on the console. |

The whole file is checked before anything starts: every listed module is
configured with its options as it will run and wired into the graph, so a
module missing an input (`projection` for `analysis`,
`params.xlma_stat.input_dir` for `xlma_stat`) is refused now, not mid-run — and
so are the source and, when `radar_command` has an `api:` part, its settings in
the environment: the base URL (`https://`), the key and a readable CA
certificate file, each in the variable the part names (`base_url_env`,
`api_key_env`, `ca_file_env`). The environment is their only source; the file
never holds them.

### What a run does

The source provides scans; for each scan the listed modules run as one graph
(`adapt graph NAME` prints it), and each module's outputs are stored when it
finishes:

| Module | Stores |
|--------|--------|
| `ingest` | the gridded volume (NetCDF, one file per scan) |
| `detection` | its decision log (`decisions.db`) |
| `analysis` | tables `cell_stats`, `cell_adjacency` |
| `tracking` | the segmented and projected 2D fields with their cell labels (NetCDF, one file per scan); tables `cell_identity` (cell label → `cell_uid` per scan), `cell_events`, `cell_tracks`; the view `cells_by_scan`, which joins identity and statistics; its decision log |
| `cell_volume_stats` | table `cell_volume_stats`, keyed by scan and cell label |
| `target_selection` | table `target_selection`: one decision per scan (the selected `cell_uid`, or none) |
| `radar_command` | table `radar_commands`: one row per decision with its outcome — `NO_TARGET`, `TARGET_UNAVAILABLE`, `VALIDATION_FAILED`, `DUPLICATE`, `PUBLISHED` (a dry run), `STALE` (`api:` is set but the scan was older than `api.max_scan_age_seconds`: held back), `SENT` / `NOT_ACKNOWLEDGED` / `SEND_FAILED` (the radar API, only when `api:` is set: sent and acknowledged as queued — not as run by the radar; sent but not acknowledged; not sent) — and the command as sent, `{"AutoTarget": {...}}` |
| `xlma_stat` | tables `xlma_stat_minutes`, `xlma_stat_scan` |

`radar_command` also prints one line per scan on the console (the command it
wrote or sent, or why there is none). A send starts with two marks: `✓✓` sent and
acknowledged, `✓✗` sent but not acknowledged, `✗–` not sent. Read the tables through
`adapt.api.StoreClient` or the dashboard.

A module that needs the scan before (projection, and everything that reads from
it) is skipped on the first scan of a run and after a gap longer than
`global.max_scan_gap_minutes`, so a run of N scans gives N−1 decisions and
commands. A module that fails skips the modules that read from it; the scan is
recorded `failed` with the reason per module, the run goes on with the next
scan as if the failed one had not arrived, and the command exits `1` with a
summary that says so (`COMPLETED, 2 SCANS FAILED`).

`Ctrl-C` (or SIGTERM) stops the run: no new scan starts, the scan in progress is
finished and saved (a stop pressed while a scan is being read may take until
that read ends, 10–30 s on a full volume, to be acted on), and the run is
recorded `cancelled`. Only one pipeline runs at a time (PID file
`~/.adapt/pipeline.pid`; one left behind by a pipeline that has exited is
ignored).

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

The resumed run keeps the modules and params it was started with. A run whose
checkpoint does not hold the state its modules need is refused, naming them.

### Running modules over a stored run (`run.source: stored_run`)

With `source: stored_run` no scan is downloaded or read: the listed modules run
over every scan of the run `--run-id`, in scan order, and each input that no
listed module produces is read back from the store. This is how a module is
added to a run after the fact, or run again with other options:

```yaml
# rules_b.yaml: another target selection rule over a stored run
run:
  source: stored_run
  radar: KFSX
  mode: historical

modules:
  - target_selection
  - radar_command

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

- The run must be of `run.radar` and must have ended (a run still `running` is
  refused; an unknown id lists the store's runs of the radar).
- The run keeps the settings it was made with: params sections (`tracker`,
  `segmenter`, …) are refused; the options of the listed modules that own
  theirs apply.
- A listed module always runs, replacing what it stored before under the same
  key. `target_selection` and `radar_commands` rows are keyed by the hash of the
  options that made them (`config_hash`), so decisions under different rules
  sit side by side in the same tables.
- A module whose input is not in the store for a scan is skipped for that scan,
  with the reason recorded.
- `radar_command` never sends over a stored run, with or without `api:`.
- The run's checkpoint is left as it is.

---

## `adapt graph`

Print the module graph of the pipeline file `./NAME.yaml` as
[Mermaid](https://mermaid.js.org) text. Nothing runs, and the folder does not
have to be a store.

```bash
adapt graph nexrad                # print the graph
adapt graph nexrad > nexrad.mmd   # save it; open it in any Mermaid viewer
```

The graph is the one a new run of the file would build, from the modules'
declared inputs and outputs and the file's options:

- `modules` — the listed modules. Each arrow is labelled with the data one
  module hands the next, e.g. `ingest -->|grid_ds_2d, grid_ds| detection`.
- `scan` and `prior_scans` — what Adapt provides to every scan: the file, its
  identity and the machine's time as the module that reads it starts
  (`wall_time`, which `radar_command` compares with the scan's time before it
  sends), and what
  modules read back from the scans before. A module that
  needs a prior scan is skipped on the first scan and after a too-long gap, and
  so is every module downstream of it.
- `store` — what is read from the store: the cells and tracks so far (for
  `target_selection`) and, over a stored run, every input no listed module
  produces.

An option can change what a module reads: with
`radar_command.extent.vertical_band` set, `radar_command` no longer reads
`cell_volume_stats`, and the arrow is gone. A file a run would refuse — an
unknown module, a wrong option, a module listed without the one that produces
its input — is refused here with the same message, exit code `2`.

---

## `adapt dashboard`

Launch the GUI dashboard.

```bash
adapt dashboard
adapt dashboard --repo ~/adapt_output
```

| Flag | Description |
|------|-------------|
| `--repo PATH` | Pre-populate the repository path field in the GUI. |

The dashboard reads from an existing store and does not affect a running
pipeline; run it in a second terminal while the pipeline runs. Its **Start New
Pipeline…** wizard writes `dashboard.yaml` in the chosen store and runs
`adapt run dashboard` there (see [Dashboard Reference](dashboard_reference.md)).
