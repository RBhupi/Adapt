# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""format_methods: the ``methods:`` block of the run header.

The reporter lays out the lines it is handed, one ``(module name, line)`` pair
per module that names a method; what a line says is the module's own business
(``BaseModule.method_line``).
"""

import logging

from adapt.runtime.run_reporter import RunReporter, format_methods

_PAIRS = (
    ("ingest", "regrid cressman · roi dist_beam"),
    ("detection", "threshold · min_cellsize 5"),
)


def test_one_line_per_pair_under_the_title():
    assert format_methods(_PAIRS).splitlines() == [
        "methods:",
        "  ingest     regrid cressman · roi dist_beam",
        "  detection  threshold · min_cellsize 5",
    ]


def test_lines_keep_the_order_they_were_given_in():
    text = format_methods(tuple(reversed(_PAIRS)))

    assert [line.split()[0] for line in text.splitlines()[1:]] == ["detection", "ingest"]


def test_a_name_longer_than_the_column_keeps_its_line():
    # The name column is 11 wide; a longer name is never cut.
    assert format_methods((("cell_volume_stats", "x"),)) == "methods:\n  cell_volume_statsx"


def test_no_pairs_yields_empty_string():
    assert format_methods(()) == ""


def _captured_reporter() -> tuple[RunReporter, list[logging.LogRecord]]:
    captured: list[logging.LogRecord] = []

    class _Cap(logging.Handler):
        def emit(self, record):
            captured.append(record)

    log = logging.getLogger("adapt.test.run_reporter_methods")
    log.handlers[:] = [_Cap()]
    log.setLevel(logging.DEBUG)
    log.propagate = False
    return RunReporter(logger=log), captured


def test_reporter_methods_emits_console_tagged_record():
    reporter, captured = _captured_reporter()

    reporter.methods(_PAIRS)

    assert captured[0].console is True
    assert captured[0].getMessage() == format_methods(_PAIRS)


def test_reporter_methods_emits_nothing_without_pairs():
    reporter, captured = _captured_reporter()

    reporter.methods(())

    assert captured == []
