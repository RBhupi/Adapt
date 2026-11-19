# Variable Names and the Tracking Field

This page is for someone whose input files name the radar variables
differently from NEXRAD, or who wants cells detected and tracked on a field
other than reflectivity. It assumes you have made a run and know how options
are set under `params:` in the pipeline file (see the [User Guide](USAGE.md)).
With NEXRAD Level-II input and reflectivity tracking, the defaults need no
change and you can skip this page.

Adapt uses one fixed set of variable names inside, the *canonical names*. Two
settings connect them to your data, and each is given in exactly one place:

| Setting | Option (under `params:`) | Applied where |
|---|---|---|
| The name in your files → the canonical name | `ingest.field_map`, `ingest.fields` | Once, inside the `ingest` module |
| The field cells are found and tracked on | `global.tracking_field` | Read by `detection`, `projection` and `tracking` |

Everything after `ingest` (the other modules, the stored column names, the
dashboard, target selection) uses **canonical names only** and never maps
names again.

## Canonical names

The canonical names of the radar variables are `reflectivity`, `velocity`,
`differential_reflectivity`, `spectrum_width`, `cross_correlation_ratio`,
`differential_phase`, …
The grid of cell labels is always `cell_labels`, and the grid coordinates are
always `x`, `y`, `z` and `time`; these are not options. The per-cell
statistics columns are always named `radar_<field>_<stat>` (for example
`radar_reflectivity_max`). The `radar_` prefix only marks a column as a cell
statistic; it does not say the field came from a radar.

## Renaming source variables (`ingest.field_map`)

If your input files name a field differently — ARM CMAC calls reflectivity
`corrected_reflectivity` — declare the rename once, in the pipeline file:

```yaml
params:
  ingest:
    field_map:
      corrected_reflectivity: reflectivity
      corrected_differential_reflectivity: differential_reflectivity
    fields: [reflectivity, differential_reflectivity]
```

- `field_map` maps **source name → canonical name** and is applied inside
  ingest, immediately after regridding. Map entries whose source variable
  is absent from a given file are ignored. The map you give replaces the
  default (empty) map whole.
- `fields` is the explicit keep-list (canonical names, checked after the
  renames). An empty list keeps every field the file provides. If a
  listed field is missing, the run fails loudly naming what was available.

The applied mapping is recorded as provenance: in each grid's
`source_fields_json` attribute and in the run's stored configuration
(`registry.db → runs.config_json`, under `module_params.ingest`), readable
via `StoreClient.run_config(run_id)`.

## Choosing the tracked field (`global.tracking_field`)

Detection thresholds on, projection flows on, and tracking links cells on
one canonical field — `reflectivity` by default:

```yaml
params:
  global:
    tracking_field: reflectivity   # or any canonical field, e.g. pressure
  analysis:
    radar_variables: [reflectivity, velocity]   # must include tracking_field
```

`tracking_field` is a setting of the whole run: every module that needs it
takes it from the run's shared values, so it is set once, under `global`.
Two rules are enforced when the run is configured (`adapt run` and
`adapt graph` refuse the file otherwise), each by the module it concerns:

1. `tracking_field` must be in `analysis.radar_variables` — otherwise the
   per-cell statistics the tracker reads are never computed.
2. If `ingest.fields` is non-empty, it must include `tracking_field`.

A rule applies only in a file that lists its module: a file without
`analysis` under `modules:` is not held to the first, and `ingest.fields` can
be set only in a file that lists `ingest`.

Dual-polarization fields are **optional**: a source without
`differential_reflectivity` runs the full pipeline, and the columns derived
from it do not exist for that run. `client.tables(collection)` lists the
columns each table has.

## Cell identity

A `cell_uid` is computed from the `scan_id` of the scan the cell is first seen
in and its `cell_label` there, so it depends only on the content of the input
file and the cell's label. The same input and the same options always give the
same uids, for any source and any tracked field. Runs recorded by earlier
versions keep the uids they stored (a uid is made once and never recomputed;
every table carries the `run_id` with it). To compare two runs, join on
`scan_id` and `cell_label`.

## Finding out which field a run tracked

The run's record is the source:

```python
client.run_config(run_id)          # full resolved config (dict)
client.run_tracking_field(run_id)  # the canonical tracked field
```

`run_tracking_field` also reads the record of earlier versions (runs that
recorded `var_names.reflectivity`), so old stores stay readable. The
dashboard and target selection find the field the same way.

## Compatibility notes

- **Existing stores** remain readable unchanged; canonical names equal the
  names Adapt always used.
- **A changed set of fields needs a new store**: the product tables of a
  collection keep the columns of the first run that wrote them. A run whose
  `ingest.fields`, `ingest.field_map` or `analysis.radar_variables` would add
  columns fails on every scan in an existing collection, with
  `new columns are rejected after first-frame freeze`. Make a new store folder
  (`adapt init`) for it.
- **Target selection weights**: `target_selection.score.weights` names
  columns of the per-cell snapshot; the tracked field's maximum is the
  column `field_max` (there is no `reflectivity:` or `field:` key). A weight
  on a name that is not a column is not caught when the run is configured:
  `target_selection` fails on every scan in which a cell is scored, and the
  message names the weight and the columns there are.
- **`cell_tracks.max_reflectivity`** (and `Track.max_reflectivity_dbz`,
  `FilterSpec.max_refl_*`): the value is the maximum of the run's
  *tracked field*; the name is kept for store compatibility and only
  means dBZ for reflectivity-tracked runs.

## Using a new source, in practice

1. Write (or reuse) an ingest path that reads the format.
2. Add the source's `ingest.field_map` and an explicit `ingest.fields`.
3. Pick `global.tracking_field` and match `analysis.radar_variables`.
4. Run into a **new store folder**.

No change is needed in detection, projection, tracking, the store, the API or
the dashboard.
