# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The ``target_study`` consumer: replay a rule, its variants and the oracle over a stored run.

Every rule is replayed causally — at each scan the engine sees only what a live
run would have seen — and scored against the ideal targets the completed run
reveals (see ``study``). Outputs, in the consumer's folder: ``summary.csv`` (one
row per rule, the oracle first), ``fates.csv`` (one row per ideal target per
rule) and ``summary.txt``.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from adapt.api import StoreClient
from adapt.consumers.target_selection.repository_source import build_snapshot
from adapt.consumers.target_selection.study import (
    FATES,
    ScanOutcome,
    StudyConfig,
    fates,
    ideal_targets,
    oracle,
    summarize,
    variant_rule,
)
from adapt.contracts import stat_column
from adapt.modules.target_selection.config import TSEConfig
from adapt.modules.target_selection.engine import TargetSelectionEngine
from adapt.utils.time import to_scan_iso

__all__ = ["SUMMARY_FILE", "run_study"]

SUMMARY_FILE = "summary.csv"
FATES_FILE = "fates.csv"
TEXT_FILE = "summary.txt"
_COLUMNS = ["rule", *FATES, "n_ideal", "episodes", "false_alarms", "coverage_median"]


def run_study(
    client: StoreClient,
    collection: str,
    run_id: str,
    base_rule: Mapping[str, Any],
    cfg: StudyConfig,
    output_dir: Path,
) -> Path:
    """Score ``base_rule`` (as "rule"), each variant and the oracle; return the summary's path."""
    cells = client.cells(run_id, collection)
    field_max_column = stat_column(client.run_tracking_field(run_id), "max")
    ideal = ideal_targets(cells, client.tracks(collection, run_id), field_max_column, cfg.ideal)
    life = {uid: sorted(group["scan_time"]) for uid, group in cells.groupby("cell_uid", sort=False)}
    ideal_uids = set(ideal["cell_uid"])
    rules = [("rule", TSEConfig.model_validate(dict(base_rule)))]
    rules += [(v.name, variant_rule(base_rule, v)) for v in cfg.variants]

    rows = [{"rule": "oracle", **oracle(ideal, life)}]
    per_target = []
    for name, rule in rules:
        outcomes = _replay(client, collection, run_id, rule)
        target_fates = fates(outcomes, ideal, life)
        rows.append({"rule": name, **summarize(target_fates, outcomes, ideal_uids)})
        per_target.append(target_fates.assign(rule=name))

    summary = pd.DataFrame(rows, columns=_COLUMNS)
    summary.to_csv(output_dir / SUMMARY_FILE, index=False)
    pd.concat(per_target, ignore_index=True)[["rule", "cell_uid", "fate", "coverage"]].to_csv(
        output_dir / FATES_FILE, index=False
    )
    (output_dir / TEXT_FILE).write_text(_text(run_id, cfg, summary), encoding="utf-8")
    return output_dir / SUMMARY_FILE


def _replay(
    client: StoreClient, collection: str, run_id: str, rule: TSEConfig
) -> list[ScanOutcome]:
    """What ``rule`` does at every complete scan of the run, seen causally."""
    engine = TargetSelectionEngine(rule)
    outcomes = []
    for ref in client.scan_timeline(collection, run_id, "cells_by_scan"):
        snapshot = build_snapshot(
            client,
            run_id,
            collection,
            growth_window_scans=rule.snapshot.growth_window_scans,
            trend_fields=rule.snapshot.trend_fields,
            track_summary=rule.snapshot.track_summary,
            at=ref.scan_time,
        )
        decision = engine.decide(snapshot)
        selection = decision.selection
        outcomes.append(
            ScanOutcome(
                to_scan_iso(ref.scan_time),
                None if selection is None else selection.cell_uid,
                None if selection is None else selection.reason.value,
                tuple(c.cell_uid for c in decision.candidates),
            )
        )
    return outcomes


def _text(run_id: str, cfg: StudyConfig, summary: pd.DataFrame) -> str:
    ideal = cfg.ideal
    lines = [
        f"Target selection study of run {run_id}",
        f"Ideal target: {'not a split child, ' if ideal.exclude_split else ''}lived >= "
        f"{ideal.min_scans} scans, reached >= {ideal.min_field_max:g}, peak at observation "
        f">= {ideal.min_peak_observation}. Ideal targets: {int(summary['n_ideal'].iloc[0])}.",
        "",
        summary.to_string(index=False),
    ]
    return "\n".join(lines) + "\n"
