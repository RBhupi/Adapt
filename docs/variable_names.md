# Variable Names and the Tracking Field

Adapt works with any radar source and, in principle, any 2-D scalar field.
Two independent bindings make that possible, and each lives in exactly one
place:

| Binding | Config knob (under `params:`) | Applied where |
|---|---|---|
| Source name → canonical name | `ingest.field_map`, `ingest.fields` | Once, inside the ingest module |
| Role → canonical field | `global.tracking_field` | Read by detection / projection / tracking |

Everything downstream of ingest — module code, contracts, stored column
names, the dashboard, the Target Selection Engine — speaks **canonical
names only** and never maps names again.

## Canonical names

The canonical vocabulary is simply Adapt's established names:
`reflectivity`, `velocity`, `differential_reflectivity`,
`spectrum_width`, `cross_correlation_ratio`, `differential_phase`, …
The segmentation label grid is always `cell_labels`, and the grid
coordinates are always `x`, `y`, `z` and `time` (fixed by the data
contract, not configurable). Per-cell statistics columns are always minted as
`radar_<field>_<stat>` (e.g. `radar_reflectivity_max`) — the `radar_`
prefix is a namespace, not a physics claim.

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
`differential_reflectivity` runs the full pipeline; ZDR-derived columns
simply do not exist for that run (consumers discover available columns
through the store's `table_schemas`).

## Cell identity (uid v2)

Cell uids are `hash(scan_id, cell_label)` — a pure function of the scan's
raw bytes and the cell's label. Identical input and configuration always
mint identical uids, for any source and any tracked field. Runs recorded
before v2 keep their stored uids (uids are minted once and never
recomputed; every table scopes them by `run_id`). Cross-run comparisons
join on `scan_id` + `cell_label`.

## How consumers resolve the field

Consumers never guess. The run's provenance is the truth:

```python
client.run_config(run_id)          # full resolved config (dict)
client.run_tracking_field(run_id)  # the canonical tracked field
```

`run_tracking_field` also understands pre-v2 provenance (runs that
recorded `var_names.reflectivity`), so old stores stay readable. The
dashboard backdrop, hover statistics, and the Target Selection Engine's
`CellSnapshot.field_max` all resolve through it.

## Compatibility notes

- **Existing stores** remain readable unchanged; canonical names equal the
  names Adapt always used.
- **Changed field sets need a new collection**: a collection's product
  tables freeze their column set on first write, so a run whose
  `ingest.fields` / `ingest.field_map` / `analysis.radar_variables` produces
  different columns
  is rejected by an existing collection (by design — create a new one).
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

## Onboarding a new source, in practice

1. Write (or reuse) an ingest path that reads the format.
2. Add the source's `ingest.field_map` and an explicit `ingest.fields`.
3. Pick `global.tracking_field` and match `analysis.radar_variables`.
4. Run into a **new collection**.

No detection, projection, tracking, persistence, api, or consumer code
changes are required.
