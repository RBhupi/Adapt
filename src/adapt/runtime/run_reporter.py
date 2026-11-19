# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The quiet console: a one-shot run header, a periodic progress line, and an
end-of-run summary. Plain ASCII, no colours/spinners. Lines are emitted at INFO
with ``extra={"console": True}`` so the ConsoleFilter lets them through while
routine per-scan logs stay in the file/JSON log only.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from adapt.contracts.execution_history import RunStart, RunSummary
from adapt.contracts.observability import SpanRecord

__all__ = [
    "RunReporter",
    "format_header",
    "format_methods",
    "format_summary",
    "format_duration",
    "format_seconds",
    "format_scan",
    "format_banner",
]

_RULE = "─" * 60
_LICENSE_URL = "https://arm-doe.github.io/Adapt/license.html"


def format_banner(version: str) -> str:
    """The plain name/version/copyright block the CLI prints first, before anything else."""
    return "\n".join(
        [
            f"ARM Adapt v{version}",
            "Copyright © 2026, UChicago Argonne, LLC",
            "All Rights Reserved",
            "By: Argonne National Laboratory",
            f"See the LICENSE ({_LICENSE_URL}) for terms and disclaimer.",
        ]
    )


def format_duration(seconds: float) -> str:
    """Human-compact duration, e.g. 862 -> '14m22s', 45 -> '45s', 3725 -> '1h2m5s'."""
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes or hours:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return "".join(parts)


def format_seconds(seconds: float) -> str:
    """Compact duration that keeps sub-second resolution: ms under 1s (so fast stages
    don't collapse to '0s'), tenths under a minute, then h/m/s.
    """
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    return format_duration(seconds)


def format_header(start: RunStart) -> str:
    """One compact block summarising the run at a glance."""
    p = start.provenance
    commit = p.git_commit[:7] if p.git_commit else "—"
    return "\n".join(
        [
            f"── Adapt run {start.run_id} {_RULE[: max(0, 48 - len(start.run_id))]}",
            f"site {start.site} · {start.instrument} · mode {start.mode}",
            f"pipeline {start.pipeline} v{start.pipeline_version} · commit {commit} "
            f"· python {p.python_version} · {p.platform}",
            f"config {start.configuration_file} (sha {start.configuration_hash[:8]}) "
            f"· modules: {', '.join(start.enabled_modules)}",
            _RULE,
        ]
    )


def format_methods(lines: Iterable[tuple[str, str]]) -> str:
    """The ``methods:`` block: one line per ``(module name, its method line)`` pair.

    The pairs come from the modules themselves (``BaseModule.method_line``), in
    pipeline order. Returns ``""`` when there are none (so the caller can skip
    emitting anything).
    """
    body = [f"  {name:<11}{text}" for name, text in lines]
    return "\n".join(["methods:", *body]) if body else ""


def _headline(summary: RunSummary) -> str:
    """SUCCESS / CANCELLED / FAILED, or the failed scans of a run that went through."""
    if summary.status == "success" and summary.failures:
        scans = "SCAN" if summary.failures == 1 else "SCANS"
        return f"COMPLETED, {summary.failures} {scans} FAILED"
    return summary.status.upper()


def format_summary(summary: RunSummary) -> str:
    """One compact block of end-of-run stats with a per-module timing table."""
    module_lines = [
        f"  {name:<18} {calls:>4} · {format_seconds(total):>7} · "
        f"{format_seconds(total / calls if calls else 0.0)}"
        for name, calls, total in summary.module_stats
    ]
    if not module_lines:
        module_lines = ["  —"]
    return "\n".join(
        [
            f"── run {summary.run_id}  {_headline(summary)}  "
            f"{format_duration(summary.duration_seconds)} {_RULE[:20]}",
            f"files {summary.files_processed:,} · scans {summary.scans_processed:,} "
            f"· objects {summary.objects_detected:,} · warnings {summary.warnings} "
            f"· errors {summary.errors} · failures {summary.failures}",
            f"scan time avg {summary.average_scan_time:.2f}s "
            f"· max {summary.maximum_scan_time:.2f}s",
            "per module (calls · total · avg):",
            *module_lines,
            _RULE,
        ]
    )


def format_scan(scan_id: str, spans: list[SpanRecord], n_cells: int) -> str:
    """One compact per-scan line, built from the scan's drained module spans.

    Reads ``stage duration`` straight off the captured telemetry — so the console
    reports what ran and how long without any module emitting its own progress.
    """
    stages = "  ".join(f"{s.name} {format_seconds(s.duration_s)}" for s in spans)
    total = sum(s.duration_s for s in spans)
    return f"scan {scan_id}  │  {stages}  │  {n_cells} cells  {format_seconds(total)}"


class RunReporter:
    """Emits the console-tagged line groups: header, per-scan progress, summary."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("adapt.run")

    def header(self, start: RunStart) -> None:
        self._log.info(format_header(start), extra={"console": True})

    def methods(self, lines: Iterable[tuple[str, str]]) -> None:
        text = format_methods(lines)
        if text:
            self._log.info(text, extra={"console": True})

    def progress(self, text: str) -> None:
        self._log.info(text, extra={"console": True})

    def scan(self, scan_id: str, spans: list[SpanRecord], n_cells: int) -> None:
        self._log.info(format_scan(scan_id, spans, n_cells), extra={"console": True})

    def summary(self, summary: RunSummary) -> None:
        self._log.info(format_summary(summary), extra={"console": True})
