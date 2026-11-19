# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``RadarProcessor.method_lines``: the run header's method lines of its modules.

Each module says its own line from the config the processor built for it; the
processor hands them on in the order it lists its modules.
"""

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]

_DEFAULT_LINES = {
    "ingest": "regrid cressman · roi dist_beam",
    "detection": "threshold · min_cellsize 5",
    "projection": "adapt_default · horizon 15min · steps 3",
    "tracking": "gap 10min · u≤0.6 · c≤1.8 · vmax 40m/s",
}


def test_every_module_with_a_method_gives_its_line(make_processor):
    """Analysis and cell_volume_stats run too but have no method to name."""
    assert dict(make_processor().method_lines) == _DEFAULT_LINES


def test_lines_come_in_the_order_the_modules_are_listed(make_processor):
    proc = make_processor()

    assert [name for name, _ in proc.method_lines] == [
        name for name in proc.module_names if name in _DEFAULT_LINES
    ]


def test_only_the_modules_that_run_give_a_line(make_processor, pipeline_config):
    proc = make_processor(config=pipeline_config.model_copy(update={"modules": ["ingest"]}))

    assert proc.method_lines == (("ingest", _DEFAULT_LINES["ingest"]),)


def test_a_line_follows_the_run_config(make_processor, pipeline_config):
    """Tracking's gap is the run's one scan-gap bound, as tracking was given it."""
    shared = pipeline_config.global_.model_copy(update={"max_scan_gap_minutes": 7.5})
    proc = make_processor(config=pipeline_config.model_copy(update={"global_": shared}))

    assert dict(proc.method_lines)["tracking"].startswith("gap 7.5min · ")


def test_a_processor_with_no_modules_has_no_lines(make_processor, pipeline_config):
    proc = make_processor(config=pipeline_config.model_copy(update={"modules": []}))

    assert proc.method_lines == ()
