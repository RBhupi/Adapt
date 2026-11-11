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

`adapt --version` prints the installed version and the path Adapt was installed
to. `adapt --help` and `adapt <command> --help` describe each command from the
command line.

Exit codes: `0` success; `1` a run that did not complete or was stopped, a run
in which a scan failed, a consumer that failed, or a store error (not a store,
already a store, unknown run); `2` an invalid pipeline file or option.

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
adapt config nexrad              # (re)write ./nexrad.yaml; asks before overwriting
adapt config --show segmenter    # print options to paste into a pipeline file
```

| Argument | Description |
|----------|-------------|
| `NAME` | Pipeline template to write as `./NAME.yaml`. Available: `nexrad`. An unknown name lists the available templates. |
| `--show NAME` | Print NAME's options as commented lines to paste under the pipeline file's `params:` line (`consumers:` for a consumer), each with a one-line meaning. |

`--show` accepts:

- a **module**: `ingest`, `detection`, `projection`, `analysis`, `tracking`
  print the `params:` sections that hold their options (ingest → `reader`,
  `regridder`; detection → `segmenter`; projection → `projector`; analysis →
  `analyzer`; tracking → `tracker`); `cell_volume_stats` and `xlma_stat` print
  their own `NAME:` entry;
- a **params section**: `reader`, `downloader`, `regridder`, `segmenter`,
  `global` (settings shared by several modules), `projector`, `analyzer`,
  `tracker`, `visualization`, `logging` (`output` is accepted but says it has no
  options left: the ones it had are obsolete);
- a **consumer**: `target_selection`, `radar_command`, `plots` print their entry
  (`target_selection` and `radar_command` print a packaged example: the
  `growing_isolated` rules and a dry-run `sandbox` command setup).

An unknown name lists every valid one. The first line of the output names the
line to paste the rest under: the file's `params:` line, or its `consumers:`
line for a consumer. Paste it there as printed — the entries are indented to sit
under that line, and fragments pasted one after another load together. The
template ends with `params:`, so `adapt config --show NAME >> nexrad.yaml` pastes
a module's or section's options there too. You rarely need the whole fragment:
a name alone runs with defaults. A listed module uses its default options, and a
bare consumer line (the template's commented line, uncommented) runs with the
packaged preset `--show` prints. Give only what you change; it is laid over the
defaults part by part, so

```yaml
consumers:
  target_selection:
    duration: {max_minutes: 15}   # the preset's rule with a 15-minute cap
  radar_command:                  # the sandbox preset as packaged
```

changes the cap and keeps every other part of the preset. A misspelled option
is still refused, naming it. An entry given twice is an error, never silently
half-dropped.

---

## The pipeline file

`adapt init` and `adapt config nexrad` write this short form (shown here
without its header comments and per-module summaries). Only names are listed;
every module option not given under `params:` uses its default. The file names
no pipeline, so a copy of it is right as is.

```yaml
run:
  source: aws_nexrad          # where scans come from: aws_nexrad | local_directory
  radar: KLOT                 # radar / collection id
  mode: realtime              # realtime | historical
  start_time: null            # historical: ISO 8601 UTC, e.g. 2025-06-17T18:00:00Z
  end_time: null
  source_dir: null            # local_directory: folder of Level-II files
  max_runtime_minutes: null   # realtime: stop after this many minutes; null = until Ctrl-C
  poll_seconds: 10            # how often consumers check for new scans

core:                         # per-scan modules; Adapt orders them by their inputs and outputs
  - ingest
  - detection
  - projection
  - analysis
  - tracking

extended:                     # per-scan enrichment, after each scan is stored
  - cell_volume_stats

consumers:                    # things that act on the results while the pipeline runs
  # target_selection:         # adapt config --show target_selection
  # radar_command:            # adapt config --show radar_command
  # plots:                    # adapt config --show plots

postprocessing:               # modules run after the core run completes
  # - xlma_stat               # needs params: xlma_stat: {input_dir: ...}

params:
```

| Section | Holds |
|---------|-------|
| `run` | Where scans come from (`source`, `radar`, `mode`, `start_time`, `end_time`, `source_dir`) and launcher settings (`max_runtime_minutes`, `poll_seconds`). Times need a time zone (`Z` = UTC) and are for `mode: historical` only, with `end_time` after `start_time`; `max_runtime_minutes` is for `mode: realtime` only; `source_dir` is required by, and only for, `source: local_directory`, which needs `mode: historical`: the folder's Level-II files must be named for `run.radar` (`RADARYYYYMMDD_HHMMSS...`; hidden files are ignored), and only those within `start_time`..`end_time` are processed — a folder with no scan in the window is refused. |
| `core` | Per-scan modules (`ingest`, `detection`, `projection`, `analysis`, `tracking`). A module whose input another, unlisted, module produces is refused. |
| `extended` | Per-scan enrichment run after each scan is stored (`cell_volume_stats`). Needs `core`. |
| `consumers` | Consumer name → its options: `target_selection`, `radar_command` (needs `target_selection`), `plots` (live runs only). |
| `postprocessing` | Modules run over the run after it completes (`xlma_stat`). |
| `params` | A params section name (`segmenter`, `tracker`, `global`, …) or the name of an extended/post-processing module the file lists → options. |

Every section must be present; one whose entries are all commented out is
empty. A module under the wrong section, an unknown key anywhere (an option of
`params.cell_volume_stats` or `params.xlma_stat` included), a key given twice,
or a core module name under `params:` (its options live in its params sections)
is an error that names the valid choices. Each params section is checked against
its schema, in a live run and in a stored-run replay alike: a value out of its
bounds is refused, and so is an obsolete option (one `--show` no longer offers,
which would have no effect). Params of an extended or post-processing module the
file does not list are refused: list the module or remove them.
`params.downloader` cannot set `radar`, `mode`, `start_time` or `end_time` —
those belong to `run:`.

A store may hold several pipeline files: copy `nexrad.yaml` to `kdix.yaml`,
edit it, and run `adapt run kdix`.

---

## `adapt run`

```bash
adapt run nexrad                              # run ./nexrad.yaml
adapt run nexrad -v                           # with DEBUG logging
adapt run nexrad --run-id 2026OCT21-1052-KFSX # continue that run, or start one with this id
```

| Argument | Description |
|----------|-------------|
| `NAME` | Pipeline name: the file `./NAME.yaml` in the current folder (a store). |
| `--run-id ID` | Live run: a run already in the store is continued with its recorded modules and params — of the file only `run.poll_seconds`, `run.max_runtime_minutes`, `consumers:` and `postprocessing:` (with its params) apply, and `run.radar` must be the run's; an unknown id (format `YYYYMONDD-HHMM-RADAR`, ending with `-` and the file's `run.radar`) starts a new run with that id. Without `--run-id` a new run's id is the minute it starts; a second run started within that minute is refused (wait a minute, or name it with `--run-id`), never joined to the first. Stored-run replay: the run to replay (required). |
| `-v`, `--verbose` | DEBUG-level logging in the run's log and on the console (live runs). |

The whole file is checked before anything starts: the pipeline, every
consumer section, every module the run will run — each configured with its
options as it will run, so a module missing an input (`projection` for
`analysis`, `params.xlma_stat.input_dir` for `xlma_stat`) is refused now, not
after the run — the source, and, when `radar_command` has an `api:` section,
that its API key is set in the environment.

### Live run (`core:` lists modules)

The pipeline downloads and processes scans; once it has recorded the run, each
consumer starts on its own thread and writes under
`consumers/<run_id>/<consumer>/` in the store:

| Consumer | Writes |
|----------|--------|
| `target_selection` | `decisions.jsonl` (one decision per decided scan) and `current_selection.json` (the newest) |
| `radar_command` | `current_targets.json` (the newest command), `history/<plan_id>.json` (every command) and `history/activity.jsonl`, one event per step: `SELECTION_RECEIVED`, `PLAN_GENERATED`, `PUBLISHED` (written to the files), `SENT` / `SEND_FAILED` (the radar API, only when `api:` is set), `NO_TARGET`, `TARGET_UNAVAILABLE`, `VALIDATION_FAILED`, `DUPLICATE`, `OUT_OF_ORDER` |
| `plots` | PNG plots of each decided scan (`show_live: true` also opens a live window) |
| `target_study` | Replays only. Scores the file's `target_selection` rule, each `variants:` entry (a name plus the parts it changes) and an oracle against the run's ideal targets: `summary.csv` (one row per rule: captured with the peak scanned, captured but peak missed, lost because the radar was busy, lost outranked, never eligible, episodes, episodes on non-ideal cells, median share of life scanned), `fates.csv` (one row per ideal target per rule), `summary.txt`. `ideal:` sets what an ideal target is (defaults: not a split child, ≥ 6 scans, ≥ 45 dBZ, peak at observation ≥ 3). |

The first scan of a run is only ingested (tracking needs a pair), so a run of N
scans gives N−1 decisions, commands and plots. Each run's `consumers/<run_id>/`
also holds `pipeline.yaml`, a copy of the file that produced it.

Once the run has ended, each consumer is stopped after what it follows
(`radar_command` after `target_selection` has written its last selection) and
makes one last pass, however long its `poll_seconds`. A consumer that fails —
during the run or in that last pass — is logged as an error in the run's log
and its events; the pipeline keeps running and the command exits non-zero.
`Ctrl-C` (or SIGTERM) stops the pipeline and its consumers: no new scan starts,
the scan in progress is finished and saved (a stop pressed while a scan is being
read may take until that read ends, 10–30 s on a full volume, to be acted on;
a second `Ctrl-C` within that wait is merged into the first), and the run is
recorded `cancelled`. Press `Ctrl-C` again once the pipeline is stopping to
abandon the consumers still running. A run that went through with failed
scans is recorded `completed`, says so in its summary (`COMPLETED, 2 SCANS
FAILED`) and exits `1`. When the run completes, the `postprocessing:` modules
run over it (also after failed scans: the completed scans are there); after a
cancelled or failed run, or a stop requested while the consumers finished, they
are skipped with a message.
Only one live pipeline runs at a time (PID file `~/.adapt/pipeline.pid`; one
left behind by a pipeline that has exited is ignored).

### Stored-run replay (`core:` and `extended:` empty)

No pipeline runs. The consumers replay the stored run `--run-id` in scan order
— `radar_command` gets one command per decided scan — and then the
`postprocessing:` modules run over it. The run must be of `run.radar` and must
have ended (a run still `running` is refused; an unknown id lists the store's
runs of the radar). Each replay writes into a new folder
`consumers/<run_id>/replay-<n>/<consumer>/`, apart from the live run's
consumers and from earlier replays, with a copy of the pipeline file that
produced it (`pipeline.yaml`), so replays of different rules can be told apart.
A replay never sends to the radar API (the `api:` section is dropped, with a
message), and `plots` is refused. `Ctrl-C` stops a replay, naming the partial
folder.

Of the `run:` section a replay uses `radar` (the run's collection); the other
keys are still checked as a run's would be, so the simplest is to copy a live
file. To compare target-selection rules, replay the same run with a different
`target_selection` section (`snapshot.track_summary: end_of_run` gives the
oracle that sees each track's final values, for a hindsight baseline):

```yaml
# rules_a.yaml: replay target selection and the radar commands it would send
run:
  source: aws_nexrad
  radar: KFSX
  mode: realtime
core: []
extended: []
consumers:
  target_selection:           # paste from: adapt config --show target_selection
    entry: {...}              # 1. gates that make a cell a candidate
    score: {...}              # 2. weights that rank the candidates (+ site bonuses)
    hold: {...}               # 3. gates the current target must keep passing
    switch: {...}             # 4. whether a newcomer may displace it, and by what margin
    duration: {...}           # 5. how long a target is followed at most
    snapshot: {...}
  radar_command:              # paste from: adapt config --show radar_command
    planning: {...}
    radar: {...}
    extent: {...}
postprocessing: []
params:
```

```bash
adapt run rules_a --run-id 2026OCT21-1052-KFSX    # -> consumers/2026OCT21-1052-KFSX/replay-1/
```

To score rules in hindsight instead of inspecting one replay, add a `target_study`
section next to `target_selection`: every variant names the parts of the rule it
changes, and the study replays the rule, each variant and the oracle (a scheduler
that knows every track's future) against the ideal targets the completed run
reveals:

```yaml
  target_study:
    ideal: {min_scans: 6, min_field_max: 45, min_peak_observation: 3, exclude_split: true}
    variants:
      - name: switch if 20 points better
        switch: {allow: true, margin: 20}
      - name: no duration cap
        duration: {max_minutes: null}
      - name: release when the 40 dBZ core vanishes
        hold: {gates: [{field: area_40dbz_km2, op: gt, value: 0}]}
```

```yaml
# lightning.yaml: add xLMA lightning statistics to a stored run
run:
  source: aws_nexrad
  radar: KLOT
  mode: historical
core: []
extended: []
consumers: {}
postprocessing:
  - xlma_stat
params:
  xlma_stat:
    input_dir: /data/lma      # folder of xLMA flash-sorted NetCDF files
```

```bash
adapt run lightning --run-id 2026OCT21-1052-KLOT
```

---

## `adapt graph`

Print the module graph of the pipeline file `./NAME.yaml` as
[Mermaid](https://mermaid.js.org) text. Nothing runs, and the folder does not
have to be a store.

```bash
adapt graph nexrad                # print the graph
adapt graph nexrad > nexrad.mmd   # save it; open it in any Mermaid viewer
```

The graph is built from the modules' own declared inputs and outputs, the same
way a run wires them, so it shows the modules `adapt run NAME` runs and the data
that passes between them:

- `core` — the per-scan modules, and `extended (after store write)` — the
  per-scan modules that run once the scan's core outputs are stored. Each arrow
  is labelled with the data one module hands the next, e.g.
  `ingest -->|grid_ds_2d, grid_ds| detection`.
- `scan` and `prior_scans` — what Adapt provides to every scan: the file and its
  identity, and the previous scans. A module that needs a prior scan is skipped
  on the first scan and after a too-long gap, and so is every module downstream
  of it.
- `postprocessing (after run)` — the modules run over the finished run.
- `consumers` — each reads the store, or the consumer it follows.

A file with an unknown module or consumer, a consumer listed without the one it
follows, or a module listed without the module that produces its input is refused
here with the message a run gives, exit code `2`. The options under `params:` and
under each consumer are not checked by `adapt graph`; `adapt run` checks them.

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
