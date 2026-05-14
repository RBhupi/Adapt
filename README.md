# Adapt


[![CI](https://github.com/ARM-DOE/Adapt/actions/workflows/ci.yml/badge.svg)](https://github.com/ARM-DOE/Adapt/actions?query=workflow%3ACI)
[![Codecov](https://img.shields.io/codecov/c/github/ARM-DOE/Adapt.svg?logo=codecov)](https://codecov.io/gh/ARM-DOE/Adapt)
[![CodeFactor](https://www.codefactor.io/repository/github/arm-doe/adapt/badge)](https://www.codefactor.io/repository/github/arm-doe/adapt)
[![Security](https://github.com/ARM-DOE/Adapt/actions/workflows/security-analysis.yml/badge.svg)](https://arm-doe.github.io/Adapt/)
[![Virus](https://github.com/ARM-DOE/Adapt/actions/workflows/virus.yml/badge.svg)](https://arm-doe.github.io/Adapt/)


[![Docs](https://github.com/ARM-DOE/Adapt/actions/workflows/docs.yml/badge.svg)](https://arm-doe.github.io/Adapt/)
[![PyPi release](https://github.com/ARM-DOE/Adapt/actions/workflows/pypi-release.yml/badge.svg)](https://arm-doe.github.io/Adapt/) 
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/arm-adapt?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pypi.org/project/arm-adapt/)
[![PyPI - License](https://img.shields.io/pypi/l/arm-adapt)](https://github.com/ARM-DOE/Adapt?tab=License-1-ov-file#)
[![ARM](https://img.shields.io/badge/Sponsor-ARM-blue.svg?colorA=00c1de&colorB=00539c)](https://www.arm.gov/)




**Real-time processing for informed adaptive scanning of ARM weather radar operations and field campaigns.**

`Adapt` is a configuration-driven modular framework for near real-time analysis of convective systems designed to support the adaptive sampling and study of convective storms and their life cycles. The system implements a modular pipeline that ingests radar observations, performs gridding and segmentation to identify convective cells, and maintains their identity through time using tracking. It further derives cell-level properties and motion to characterize storm evolution and generate candidate targets for adaptive radar scanning.

Adapt operates in both real-time and archival modes, producing standardized data products in the form of gridded fields, tabular summaries, and relational tracking records. Its design emphasizes reproducibility, extensibility, and consistency, allowing new analysis methods and data sources to be integrated without altering core workflows.

Currently, it ingests NEXRAD Level-II data, performs gridding/segmentation/analysis, and writes results for downstream visualization and scientific workflows.

## Installation

Create a fresh conda environment (Python 3.13) and install from PyPI:

```bash
conda create -n adapt_env python=3.13 -y
conda activate adapt_env
python -m pip install --upgrade pip
pip install arm-adapt
adapt --help
```

## Quickstart

```bash
adapt run-nexrad --radar KLOT --base-dir ~/adapt_output
adapt dashboard --repo ~/adapt_output
```

Open the dashboard in a second terminal for live viewing.



## Documentation

- Detailed usage, configuration, outputs, and troubleshooting: `docs/USAGE.md`

## Status and compatibility

**Status: Alpha.** `Adapt` is under active development and is provided for early testing and evaluation.  
**No backward compatibility is guaranteed** for code, APIs, configuration, or generated data products (e.g., SQLite/Parquet/NetCDF). Expect breaking changes between commits and releases.  
Contribution guidelines and a roadmap will be published in a future release.

## Funding

`Adapt` is supported by the U.S. Department of Energy as part of the Atmospheric Radiation Measurement (ARM), an Office of Science User Facility.

## License

BSD license; see `LICENSE` for terms and disclaimer.
