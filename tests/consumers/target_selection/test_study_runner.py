# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``run_study`` replays every variant and the oracle over a stored run and writes the tables."""

import pandas as pd
import pytest

from adapt.api import StoreClient
from adapt.consumers.target_selection.study import StudyConfig
from adapt.consumers.target_selection.study_runner import run_study
from tests.helpers import radar_store as synthetic_store
from tests.helpers.pipeline_files import TARGET_SELECTION

pytestmark = pytest.mark.unit

# The synthetic run has two cells over three scans: uid-a at 50 dBZ, uid-b at 40 dBZ.
STUDY = StudyConfig.model_validate(
    {
        "ideal": {"min_scans": 3, "min_field_max": 45.0, "min_peak_observation": 1},
        "variants": [
            {
                "name": "any core",
                "entry": {
                    "gates": [{"field": "radar_reflectivity_max", "op": "ge", "value": 35.0}]
                },
            },
        ],
    }
)


def test_the_study_writes_one_summary_row_per_rule_and_the_oracle(tmp_path):
    built = synthetic_store.build_store(tmp_path / "store", volume_stats=True)
    out = tmp_path / "study"
    out.mkdir()

    with StoreClient(built.root) as client:
        run_study(
            client, synthetic_store.COLLECTION, synthetic_store.RUN, TARGET_SELECTION, STUDY, out
        )

    summary = pd.read_csv(out / "summary.csv")
    assert list(summary["rule"]) == ["oracle", "rule", "any core"]
    assert (summary["n_ideal"] == 1).all()  # uid-a: 3 scans, 50 dBZ, not a split child
    # The file's rule (a 45 dBZ gate) and the variant both take uid-a at its 2nd observation.
    assert list(summary["captured_peak"]) == [1, 1, 1]
    fates = pd.read_csv(out / "fates.csv")
    assert set(fates["rule"]) == {"rule", "any core"}
    assert set(fates["cell_uid"]) == {"uid-a"}
    assert (out / "summary.txt").read_text().startswith("Target selection study")
