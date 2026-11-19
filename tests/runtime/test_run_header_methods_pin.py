# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Pin: the ``methods:`` block of the run header, every option at its default.

Read where a user reads it, on the console of a real run: ``start()`` of the
five core modules over a folder holding one file that is not a radar volume, so
the header is printed and ingest then refuses the scan.

The block has one line per module that has a method, in the order the header
lists the run's modules. That is the order the modules were registered in this
process: ingest, detection, projection, tracking in an ``adapt run``, another
order when a test's imports register them first.
"""

import logging
import re

import pytest

from adapt.configuration.pipeline_file import PipelineFile
from adapt.configuration.pipeline_run import run_config
from adapt.persistence.store import init_store
from adapt.runtime.orchestrator import PipelineOrchestrator

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]

MODULES = ["ingest", "detection", "projection", "analysis", "tracking"]
# Analysis has no method to name, so no line.
LINES = {
    "ingest": "  ingest     regrid cressman · roi dist_beam\n",
    "detection": "  detection  threshold · min_cellsize 5\n",
    "projection": "  projection adapt_default · horizon 15min · steps 3\n",
    "tracking": "  tracking   gap 10min · u≤0.6 · c≤1.8 · vmax 40m/s\n",
}


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """start() configures and shuts down root logging; give pytest its handlers back."""
    root = logging.getLogger()
    saved = root.handlers[:]
    saved_level = root.level
    yield
    root.handlers[:] = saved
    root.setLevel(saved_level)


def test_the_run_header_names_each_method_and_its_key_choices(tmp_path, capsys):
    scans = tmp_path / "scans"
    scans.mkdir()
    (scans / "KTST20240518_120000_V06").write_bytes(b"not a radar volume")
    pipeline = PipelineFile.model_validate(
        {
            "run": {
                "source": "local_directory",
                "source_dir": str(scans),
                "radar": "KTST",
                "mode": "historical",
                "start_time": "2024-05-18T11:00:00Z",
                "end_time": "2024-05-18T13:00:00Z",
            },
            "modules": MODULES,
            "params": {},
        }
    )
    store_root = init_store(tmp_path / "store")

    PipelineOrchestrator(run_config(pipeline, store_root / "case.yaml")).start()

    console = capsys.readouterr().err
    listed = re.search(r"· modules: (.+)\n", console).group(1).split(", ")
    # The block: its title, then every indented line that follows it.
    block = re.search(r"methods:\n(?:  .*\n)*", console).group()
    assert block == "methods:\n" + "".join(LINES[name] for name in listed if name in LINES)
