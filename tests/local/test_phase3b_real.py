"""[local] Offline plan on the real canonical data: probe <= 40 credits, frozen population."""

from __future__ import annotations

import pytest

from quantedge.canonical.build import load_tables
from quantedge.core.dataview import DataView
from quantedge.core.splits import load_split_config
from quantedge.curation.eligibility import build_eligibility
from quantedge.odds.planner import build_plan
from quantedge.odds.preregistration import load_protocol
from tests.conftest import REPO_ROOT
from tests.local.test_real_snapshot import BUILD_DIR

pytestmark = pytest.mark.local


def test_real_plan() -> None:
    protocol, _ = load_protocol(
        REPO_ROOT / "configs" / "preregistration" / "market_benchmark_v1.yaml"
    )
    view = DataView(
        load_tables(BUILD_DIR), load_split_config(REPO_ROOT / "configs" / "splits.yaml")
    )
    matches, tournaments = view.table("matches"), view.table("tournaments")
    plan = build_plan(matches, tournaments, build_eligibility(matches, tournaments), protocol)
    probe = plan["probe"]
    assert probe["total_credits"] == 40 and len(probe["requests"]) == 4
    assert {r["edition"].split()[-1] for r in probe["requests"]} == {"2022"}
    pilot = plan["pilot_estimate"]
    assert pilot["expected_eligible_population"] == 1228
    assert len(pilot["editions"]) == 10
    assert all(e["snapshot_cadence_minutes"] == 10 for e in pilot["editions"])  # all pre-Sept-2022
