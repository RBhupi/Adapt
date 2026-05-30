<p align="center">
  <img src="https://img.shields.io/badge/STATUS-ACTIVE%20DEVELOPMENT-orange?style=for-the-badge&logo=github" />
  <img src="https://img.shields.io/badge/API-BREAKING%20CHANGES-red?style=for-the-badge&logo=dependabot" />
  <img src="https://img.shields.io/badge/STABILITY-ALPHA-yellow?style=for-the-badge" />
</p>

<p align="center">
  <strong>Adapt is under active development</strong>
</p>


> [!IMPORTANT]
> We are not accepting external Pull Requests at this time. Contribution guidelines will be published after our stable release.

<p align="center">
  Components are continuously being redesigned, validated, stress-tested, and integrated for real-time adaptive radar operations.
</p>

<p align="center">
  <strong>Expect frequent breaking changes</strong> in APIs, configuration files, database schemas, NetCDF/Parquet outputs, workflows, and CLI behavior until the first stable release.
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/ARM-DOE/Adapt/main/docs/_static/adapt-dev-banner.gif" width="100%">
</p>

---

# Adapt

<p align="center">
  <a href="https://github.com/ARM-DOE/Adapt/actions/workflows/ci.yml">
    <img src="https://github.com/ARM-DOE/Adapt/actions/workflows/ci.yml/badge.svg" alt="CI">
  </a>
  <a href="https://codecov.io/gh/ARM-DOE/Adapt">
    <img src="https://img.shields.io/codecov/c/github/ARM-DOE/Adapt.svg?logo=codecov" alt="Codecov">
  </a>
  <a href="https://www.codefactor.io/repository/github/arm-doe/adapt">
    <img src="https://www.codefactor.io/repository/github/arm-doe/adapt/badge" alt="CodeFactor">
  </a>
  <a href="https://github.com/ARM-DOE/Adapt/actions/workflows/security-analysis.yml">
    <img src="https://github.com/ARM-DOE/Adapt/actions/workflows/security-analysis.yml/badge.svg" alt="Security">
  </a>
  <a href="https://github.com/ARM-DOE/Adapt/actions/workflows/virus.yml">
    <img src="https://github.com/ARM-DOE/Adapt/actions/workflows/virus.yml/badge.svg" alt="Virus">
  </a>
  <br>
  <a href="https://github.com/ARM-DOE/Adapt/actions/workflows/docs.yml">
    <img src="https://github.com/ARM-DOE/Adapt/actions/workflows/docs.yml/badge.svg" alt="Docs">
  </a>
  <a href="https://github.com/ARM-DOE/Adapt/actions/workflows/pypi-release.yml">
    <img src="https://github.com/ARM-DOE/Adapt/actions/workflows/pypi-release.yml/badge.svg" alt="PyPI Release">
  </a>
  <a href="https://pypi.org/project/arm-adapt/">
    <img src="https://static.pepy.tech/personalized-badge/arm-adapt?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads" alt="Downloads">
  </a>
  <a href="https://github.com/ARM-DOE/Adapt?tab=License-1-ov-file">
    <img src="https://img.shields.io/pypi/l/arm-adapt" alt="License">
  </a>
  <a href="https://www.arm.gov/">
    <img src="https://img.shields.io/badge/Sponsor-ARM-blue.svg?colorA=00c1de&colorB=00539c" alt="ARM">
  </a>
</p>

---

## Overview

Real-time processing for informed adaptive scanning of ARM weather radar operations and field campaigns.

`Adapt` is a configuration-driven modular framework for near real-time analysis of convective systems designed to support adaptive sampling and study of convective storms and their life cycles. The system implements a modular pipeline that ingests radar observations, performs gridding and segmentation to identify convective cells, and maintains their identity through time using tracking. It further derives cell-level properties and motion to characterize storm evolution and generate candidate targets for adaptive radar scanning.

Adapt operates in both real-time and archival modes, producing standardized data products in the form of gridded fields, tabular summaries, and relational tracking records. Its design emphasizes reproducibility, extensibility, and consistency, allowing new analysis methods and data sources to be integrated without altering core workflows.

Currently, it ingests NEXRAD Level-II data, performs gridding, segmentation, and analysis, and writes results for downstream visualization and scientific workflows.

---

## Installation

Create a fresh conda environment (Python 3.13) and install from PyPI:

```bash
conda create -n adapt_env python=3.13 -y
conda activate adapt_env
python -m pip install --upgrade pip
pip install arm-adapt
adapt --help
```

---

## Quickstart

```bash
adapt run-nexrad --radar KLOT --base-dir ~/adapt_output
```

and then open the dashboard in a second terminal for live viewing.
```bash
adapt dashboard --repo ~/adapt_output
```

---

## Documentation

Detailed usage, configuration, outputs, and troubleshooting: [docs/USAGE.md](docs/USAGE.md)

---

## Status and Compatibility

- **Status:** Alpha. Adapt is under active development and provided for early testing and evaluation.
- No backward compatibility is guaranteed for code, APIs, configuration, or generated data products (e.g., SQLite, Parquet, NetCDF).
- Expect breaking changes between commits and releases.

---

## Funding

Adapt is supported by the U.S. Department of Energy as part of the Atmospheric Radiation Measurement (ARM) User Facility within the Office of Science.

---

## License

Copyright © 2026, UChicago Argonne, LLC
See [LICENSE](LICENSE) for terms and disclaimer.
