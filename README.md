# Adapt

**Real-time detection, projection, and tracking of convective storms in weather radar data — built for operational adaptive scanning at the DOE ARM User Facility, and for large-scale generation of value-added products and AI-ready storm datasets.**

![Status](https://img.shields.io/badge/STATUS-ACTIVE%20DEVELOPMENT-orange?style=for-the-badge&logo=github)
![API](https://img.shields.io/badge/API-BREAKING%20CHANGES-red?style=for-the-badge&logo=dependabot)
![Stability](https://img.shields.io/badge/STABILITY-ALPHA-yellow?style=for-the-badge)

**Health**

[![CI](https://github.com/ARM-DOE/Adapt/actions/workflows/ci.yml/badge.svg)](https://github.com/ARM-DOE/Adapt/actions/workflows/ci.yml)
[![Docs](https://github.com/ARM-DOE/Adapt/actions/workflows/docs.yml/badge.svg)](https://github.com/ARM-DOE/Adapt/actions/workflows/docs.yml)
[![codecov](https://img.shields.io/codecov/c/github/ARM-DOE/Adapt.svg?logo=codecov)](https://codecov.io/gh/ARM-DOE/Adapt)
[![CodeFactor](https://www.codefactor.io/repository/github/arm-doe/adapt/badge)](https://www.codefactor.io/repository/github/arm-doe/adapt)

**Install**

[![PyPI](https://img.shields.io/pypi/v/arm-adapt?logo=pypi&logoColor=blue)](https://pypi.org/project/arm-adapt/)
[![conda-forge](https://img.shields.io/conda/vn/conda-forge/arm-adapt.svg?logo=anaconda&logoColor=green)](https://anaconda.org/conda-forge/arm-adapt)
[![Python](https://img.shields.io/pypi/pyversions/arm-adapt?logo=python&logoColor=white)](https://pypi.org/project/arm-adapt/)
[![Platforms](https://img.shields.io/badge/platforms-linux%20%7C%20macOS%20%7C%20windows-peru)](https://arm-doe.github.io/Adapt/installation.html)

**Project**

[![PyPI Downloads](https://img.shields.io/pypi/dm/arm-adapt.svg?logo=pypi&logoColor=white)](https://pypi.org/project/arm-adapt/)
[![Conda Downloads](https://img.shields.io/conda/dn/conda-forge/arm-adapt.svg?logo=anaconda&logoColor=white)](https://anaconda.org/conda-forge/arm-adapt)
[![License](https://img.shields.io/pypi/l/arm-adapt?logo=opensourceinitiative&logoColor=white)](https://github.com/ARM-DOE/Adapt?tab=License-1-ov-file)
[![ARM Sponsor](https://img.shields.io/badge/Sponsor-ARM-blue.svg?colorA=00c1de&colorB=00539c)](https://www.arm.gov/)


> **Note.** Adapt is under active development. External pull requests are not being accepted at this
> time; contribution guidelines will follow the first stable release. APIs, configuration files,
> database schemas, and output formats are subject to change without notice.

**Documentation:** [arm-doe.github.io/Adapt](https://arm-doe.github.io/Adapt/) — [Installation](https://arm-doe.github.io/Adapt/installation.html) · [User Guide](https://arm-doe.github.io/Adapt/USAGE.html) · [Results](https://arm-doe.github.io/Adapt/outputs.html) · [CLI Reference](https://arm-doe.github.io/Adapt/cli_reference.html)

---

## What Adapt does

Adapt reads weather radar volume scans one after the other, from files on disk, from the public
NEXRAD archive, or as they arrive. For each scan it grids the volume, finds the convective cells,
estimates their motion, measures each cell, and links it to the cells of the scan before. A storm
keeps one identity for its whole life, through splits and merges. Adapt can then choose the cell a
radar should follow and write the command that points the radar at it.

What comes out of a run:

| Output | Form |
|--------|------|
| The gridded volume of every scan | NetCDF, one file per scan |
| The cell labels, the motion field and the projected cell positions | NetCDF, one file per scan |
| One row per cell and scan: identity, area, reflectivity and other radar statistics, heights and volumes | SQLite tables, read as one table through the view `cells_by_scan` |
| One row per track and one row per event (a cell begins, continues, splits, merges, ends) | SQLite tables |
| The options every run was made with, and what each step did on each scan | SQLite tables |

Everything goes into one folder, the *store*, and is read with Python (`adapt.api.StoreClient`), with
SQL, or in the dashboard. The same scans and the same options give the same cells and the same
identities, run after run.

## Quick start

```bash
conda create -n adapt_env python=3.13 -y
conda activate adapt_env
conda install conda-forge::arm-adapt

mkdir my_case && cd my_case
adapt init          # makes the folder a store and writes the pipeline file nexrad.yaml
                    # edit the run: section of nexrad.yaml: your radar, and where the scans come from
adapt run nexrad    # process the scans
adapt dashboard     # look at the result
```

`nexrad.yaml` is the *pipeline file*. It says where the scans come from, lists the steps to run (the
*modules*), and holds the options you change:

```yaml
run:
  source: local_directory          # or aws_nexrad, the public NEXRAD archive
  radar: KHTX
  mode: historical
  start_time: 2026-08-04T19:00:00Z
  end_time: 2026-08-04T21:00:00Z
  source_dir: /data/level2/KHTX
  max_runtime_minutes: null

modules:
  - ingest                    # read + regrid radar volumes
  - detection                 # segment cells from the grid
  - projection                # optical-flow projection between scans
  - analysis                  # 2D per-cell statistics
  - tracking                  # link cells across scans
  - cell_volume_stats         # 3D volume stats per cell (base, top, echo-top heights)

params:
  detection:
    threshold_params: {threshold: 40}   # an option you change; everything else keeps its default
```

The [User Guide](https://arm-doe.github.io/Adapt/USAGE.html) walks through a first run, reading the
results, changing options, adding modules, running a module later over a stored run, and resuming a
stopped run.

## How it is put together

Each step is a *module* with declared inputs and outputs. A pipeline file lists modules by name, and
Adapt orders them by what each reads and writes. A new step is a new module: no existing module
changes. Modules that ship with Adapt: `ingest`, `detection`, `projection`,
`analysis`, `tracking`, `cell_volume_stats`, `target_selection`, `radar_command` and `xlma_stat`
(lightning statistics from a Lightning Mapping Array). See the
[Module Developer Guide](https://arm-doe.github.io/Adapt/module-developer-guide.html) to write one.

## Motivation

Adapt is developed to support adaptive scanning at the U.S. Department of Energy's Atmospheric Radiation
Measurement (ARM) User Facility, where the C-Band Scanning ARM Precipitation Radar (CSAPR2) requires a
real-time source of high-value scan targets, identified and prioritized automatically rather than by a
fixed volume-scan schedule. The same pipeline runs unchanged on historical data, supporting research
applications such as storm lifecycle studies, radar–lightning correlation analysis, and the construction
of analysis-ready, multi-sensor datasets combining radar, satellite, and Lightning Mapping Array
observations.

## Current status

Adapt is at an alpha stage of development.

- The NEXRAD pipeline (ingest, detection, projection, analysis, tracking) runs end to end in
  real-time and historical modes.
- A read-only Python API and a GUI dashboard give access to the results.
- Modules can be run later over a stored run, for example to add lightning statistics to tracked cells.
- Rule-based target selection and radar commands for adaptive scanning are under active development.
- Supported platforms: macOS, Linux, and Windows. See [Installation](https://arm-doe.github.io/Adapt/installation.html).

No backward compatibility is guaranteed for APIs, configuration, or generated data products until the
first stable release.

## Roadmap

Planned work extends Adapt toward a general, multi-sensor storm-analysis framework: pluggable tracking
and segmentation backends, a data-agnostic variable layer so new instruments are a configuration change
rather than a code change, and integration with ARM radar-control systems to close the loop between
detection and adaptive scanning. See [Vision](https://arm-doe.github.io/Adapt/vision.html) for details.

---

## Funding

Adapt is supported by the U.S. Department of Energy as part of the Atmospheric Radiation Measurement
(ARM) User Facility, within the Office of Science.

## License

Copyright © 2026, UChicago Argonne, LLC.
See [LICENSE](https://github.com/ARM-DOE/Adapt/blob/main/LICENSE) for terms and disclaimer.
