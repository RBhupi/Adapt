# Installation

This page is for anyone installing Adapt for the first time. It assumes you can
use a terminal and have `conda` (or `mamba`). When `adapt --version` works, go
on to the [User Guide](USAGE.md) for a first run.

## Requirements

- Python 3.10 or later
- macOS, Linux, or Windows
- Internet access, to download NEXRAD data from the public archive on AWS and
  map tiles for the dashboard. Processing files you already have on disk needs
  none.
- Disk space: with the default grid, about 140 MB per processed scan.

---

## Recommended: conda-forge

Adapt is published on [conda-forge](https://anaconda.org/conda-forge/arm-adapt) for macOS, Linux, and
Windows. Installing from there keeps every compiled dependency (netCDF4, h5py, opencv, Py-ART) on
conda-native builds, which avoids the binary conflicts described below:

```bash
conda create -n adapt_env python=3.13 -y
conda activate adapt_env
conda install conda-forge::arm-adapt
```

Verify:

```bash
adapt --version
adapt --help
```

`adapt --version` prints the installed version and the path Adapt was installed to, which confirms
which environment is active.

---

## Alternative: pip

```bash
pip install arm-adapt
```

The environment must be **fresh**: do not `pip install` into a conda environment that already has
netCDF4, h5py, opencv, or Py-ART installed from conda-forge. Adapt's compiled dependencies ship both
conda and pip builds of the same native libraries, and mixing them fails at import with a DLL error
(on Windows, `[WinError 11] An attempt was made to load a program with an incorrect format`). On
Windows, use 64-bit x86 Python: several of these dependencies publish no ARM64 wheels. This is a
pip/conda mixing issue only; it does not apply to the conda-forge install above.

---

## Developers: latest source

To run the version on `main` and have your edits take effect immediately, install the checkout in
editable mode:

```bash
conda create -n adapt_dev python=3.13 -y
conda activate adapt_dev
git clone https://github.com/ARM-DOE/Adapt.git
cd Adapt
pip install -e .
```

Use a fresh environment for the editable install too: the same pip/conda binary-mixing issue
described above applies.

---

## Optional: map overlay in the dashboard

The dashboard draws a background map when `contextily` is installed. Without it the dashboard works
fully (radar data, cells, statistics), with no map behind them. Install it with the extra:

```bash
pip install "arm-adapt[maps]"
```

> **Note for conda users:** install `pyproj` and `contextily` via conda-forge instead of pip to
> avoid PROJ database version conflicts:
>
> ```bash
> conda install -c conda-forge pyproj contextily
> ```

---

## First run

```bash
mkdir my_case && cd my_case
adapt init                                # makes the folder a store and writes nexrad.yaml
```

Then follow *First run* in the [User Guide](USAGE.md).

---

## Troubleshooting

### `adapt: command not found`

Activate the environment first:

```bash
conda activate adapt_env
```

### PROJ database version errors

```
PROJ: proj.db contains DATABASE.LAYOUT.VERSION.MINOR = 4 whereas >= 6 is expected
```

pip-installed `pyproj` bundles an outdated PROJ database. Fix:

```bash
pip uninstall pyproj -y
conda install -c conda-forge pyproj
```

### pyproj network warning

```
UserWarning: pyproj unable to set PROJ database path.
```

Harmless. Suppress with:

```bash
conda env config vars set PROJ_NETWORK=OFF -n adapt_env
```

### `ModuleNotFoundError` on dashboard startup

Adapt is not installed in the active environment: a clone of the repository alone is not an
installation. Install it:

```bash
pip install arm-adapt        # or, in a clone: pip install -e .
```
