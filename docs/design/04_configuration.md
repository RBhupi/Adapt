# Configuration System

**Location**: `src/adapt/configuration/`

---

## Responsibility

The configuration system turns a pipeline file into a single, frozen, fully validated run config (`InternalConfig`) and one frozen config per listed module. The rest of the system uses these without further resolution logic.

It knows no module option. Every module defines its own options (one pydantic model in `modules/<name>/config.py`); the configuration system finds the listed modules in the registry, lays the file's values over each module's defaults, validates the result with the module's model, and hands it to the module.

Once the run config and the module configs are built, configuration is done. No component reads the pipeline file or applies a default value at runtime.

---

## What It Is Not Responsible For

- Defining module options, their defaults or their bounds — each module does, in its own options model
- Creating the store — that is `adapt init`'s job; a run in a folder that is not a store is refused
- Validating scientific correctness of parameter values — it validates names, types and ranges, not physical sense
- Keeping the user's pipeline file — the file is the user's; a run records a copy of it with its config
- Providing dynamic configuration that can change at runtime — the frozen config is immutable

---

## The Layers

```
--set path=value   ← planned for the next stage; not implemented
    ↓ overrides
Pipeline file      ← params.<module>, params.global, params.downloader, params.logging
    ↓ overrides
Module defaults    ← each module's options model, or its packaged preset
    ↓
InternalConfig     ← single frozen result, with every listed module's options
```

A value set in the pipeline file wins over the module's default. If the file does not set a value, the default from the module's options model is used (for `target_selection` and `arm_radar_comm`, the default is the packaged preset).

This means a pipeline file holds only what its author changes. A module listed under `modules:` with no `params:` entry runs with its defaults.

A command-line layer, one generic `--set path=value` on `adapt run` and `adapt graph`, is planned as the next stage. It will be one more layer laid over the file's values before validation. It does not exist yet.

---

## Components

### The pipeline file (`pipeline_file.py`)

One YAML file per pipeline, in the store folder. Three sections, all required:

```
run:       # where scans come from
    source             # aws_nexrad | local_directory | stored_run
    radar              # radar / collection id
    mode               # realtime | historical
    start_time         # historical only
    end_time           # historical only
    source_dir         # local_directory only
    max_runtime_minutes  # realtime only

modules:   # the per-scan modules to run, by name, in any order

params:    # options; every key is optional
    global:            # the settings of the whole run
        tracking_field       # canonical field detection, projection and tracking use
        z_level              # analysis altitude (m)
        max_scan_gap_minutes # largest gap between two scans any module pairs across
    downloader:        # polling and queue settings of the scan source
    logging:           # log levels and telemetry switches
    <module>:          # the options of a listed module, under its own name
```

`load_pipeline_file` reads and validates the structure: the three sections, the run section's rules, known and listed module names, and the `params:` keys (`global`, `downloader`, `logging`, or a listed module). Unknown keys and keys given twice raise a `ConfigError` immediately — no silent ignoring of typos.

### Module options (`modules/<name>/config.py`)

Not part of this package, but the other half of the contract. Each module's options are one pydantic model: frozen, unknown keys refused, every option with its default, bounds and description. The node declares it as `options_class`. The Module Developer Guide (`docs/module-developer-guide.md`) describes the contract.

### The module resolver (`schemas/module_resolver.py`)

The **single generic path** from a file's values to a module's config. It never names a module; it finds them in the registry.

- `module_options(name, given, source=...)`: the module's defaults (its options model, or its packaged preset) with `given` laid over, validated with the module's `options_class`.
- `resolve_module_configs(run_config, modules)`: for each module about to run, `module_options` from the run's recorded options, then the module's `configure(options, shared)`, which returns the frozen config `run()` receives.

`shared` is `adapt.contracts.SharedConfig`: the values of the whole run (`tracking_field`, `z_level`, `max_scan_gap_minutes`, `source`). A module that needs one takes it there, in `configure`; it never reads another module's options.

**Merge rules** (the file's values laid over the defaults, by the model's schema):
- A part that is itself a model of options: merged key by key, so one key deep inside can be given alone. A part that is off (`null`) in the defaults has nothing to merge into: all of its required keys must be given
- Lists, and options that are mappings of free keys: replaced entirely (the given value replaces the default, not extends it)
- Scalar values: replaced
- An explicit `null`: taken as the value, then validated — refused for an option that does not allow it, never read as "use the default"

There are no special merge rules outside the modules. A rule that concerns one module lives in that module's `configure`: for example, `analysis` always excludes a fixed set of internal variables and adds the user's `exclude_fields` to them, and refuses a run whose `tracking_field` is not in its `radar_variables`.

### InternalConfig (`schemas/internal.py`)

The final, frozen run config. This is a pydantic model with `frozen=True`. Once created, no component can modify it.

```
mode, source, source_dir   # from the file's run section
base_dir                   # the store root: the folder that holds the file
run_id                     # e.g. "2026OCT21-1052-KFSX"
global_                    # tracking_field, z_level, max_scan_gap_minutes
downloader                 # with radar, mode and the time window from the run section
logging
modules                    # the listed modules
module_params              # every listed module's validated options, defaults included
pipeline                   # provenance: the pipeline file's name, path and content
```

It holds no section per module: a module's options are only in `module_params`, under the module's name, as its own model validated them.

### run_config (`pipeline_run.py`)

The **one entrypoint** from a loaded pipeline file to a run's `InternalConfig`. `adapt run` calls it; `adapt graph` calls its store-free counterpart `configured_modules`.

- **New run**: a new run id (the minute it starts, or `--run-id`), every listed module's options validated and recorded.
- **Resumed run** (`--run-id` of a run in the store): the recorded config, with only `base_dir` set to where the store is now. The run is configured from its recorded options, not from the file: the file's options are not read, so they are not checked.
- **Stored-run pass** (`run.source: stored_run`): the stored run's recorded config, with the file's module list. A listed module starts from its own defaults, then the file. `params.global`, `params.downloader` and `params.logging` are refused. If the stored run already ran a listed module with other options, the pass is refused, unless the module's rows are keyed by its options (`rows_keyed_by_options`: `target_selection`, `arm_radar_comm`).

In every case all listed modules are then configured and wired into the graph, so a wrong option or a module missing an input is refused before anything starts.

A recorded config that this version cannot read (for example, of a run recorded before each module's options moved under the module's name) is a `ConfigError` that names the run and the keys; such a run cannot be resumed or used for a stored-run pass.

### Errors (`schemas/errors.py`)

`validated()` turns a pydantic `ValidationError` into a `ConfigError` (`OptionsRefused`) with one line per option: its full path and the reason. For a module option, `module_options` adds a last line that names the command that prints the module's options.

```
Invalid configuration (pipeline file /data/kilx/nexrad.yaml):
  • params.detection.h_maxima — value -1 is out of range — Input should be greater than 0
The options of detection: adapt config --params detection
```

The options of a file that fail validation are reported together (`together()`): one message holds those of every listed module and of `global`, `downloader` and `logging`, then one such last line per module that has one. What a module's `configure` refuses, a module missing an input, and a missing time window are reported once the options validate.

The options are recorded with the run as JSON and the run is configured from that record, so a value the record would not give back is refused when the run is configured: an infinite or NaN number, for a module option and for `global.z_level` and `global.max_scan_gap_minutes`.

### Templates and the module catalog (`pipeline_template.py`, `defaults.yaml`)

A template is the packaged file `configuration/templates/<name>.yaml`, read and checked as a pipeline file: its `run:` gives the run values, its `modules:` the modules that are on, and it sets no `params`. `pipeline_template.py` writes it out as the short, commented pipeline file a new pipeline starts from (`adapt init`, `adapt config --NAME`), with the catalog's other modules offered commented out. `defaults.yaml` is the module catalog: the node modules a pipeline file may list. It holds no option values.

---

## Configuration Persistence

The run config is recorded with the run, in the store: `registry.db`, table `runs`, column `config_json` (read it with `StoreClient.run_config(run_id)`). This means:
- Every pipeline run has a complete record of the exact settings used, defaults included
- A resumed run is configured from that record, so it runs with the same options as before the stop
- Debugging anomalous outputs always starts with "what were the parameters?"

There is no separate config file per run.

---

## Design Notes

**Why freeze InternalConfig?**

A mutable configuration object in a multi-threaded system is a race condition waiting to happen. Immutability makes it safe to pass the config object to every thread without synchronisation. It also makes the system easier to reason about: parameters do not change during a run.

**Why validate strictly (no unknown fields)?**

Silent acceptance of unknown fields masks typos. If a user writes `threshhold: 30` (double h), strict validation raises an error immediately at startup rather than silently ignoring the override and running with the default all day.

**Why are a module's options defined in the module?**

An option written in one place cannot disagree with itself. Earlier, an option of an older module was defined three times (a defaults schema, a run-config schema, the module's own config) and translated between them, and its `params:` key had another name than the module. Now adding or changing an option edits one model in one module, and nothing in `configuration/` changes.
