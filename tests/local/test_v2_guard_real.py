"""Real programme state through the V2 guard (local: needs the gitignored data/; read-only)."""

from __future__ import annotations

import pytest

from quantedge.core.lineage import LicenceClass
from quantedge.odds.store import CaptureStore
from quantedge.odds.v2_guard import preflight
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.local

DATA = REPO_ROOT / "data"
STAGES = DATA / "processed" / "odds" / "the_odds_api" / "stages"


def test_programme_state_is_consistent_and_week_two_fits_the_ceiling() -> None:
    if not (STAGES / "v2a2-uso-2021-wk2" / "manifest.json").exists():
        pytest.skip("week-2 manifest not present")
    store = CaptureStore(DATA, "the_odds_api", LicenceClass.COMMERCIAL_LICENSED, "historical")
    summary = preflight(STAGES / "v2a2-uso-2021-wk2", repo_root=REPO_ROOT, store=store)
    assert summary["programme_ceiling"] == 1430
    assert summary["spent_before"] == 70  # the locked US Open 2021 week 1
    assert summary["this_call_max"] == 70
    assert summary["remaining_after"] == 1290
    week1 = summary["stages"]["v2a2-feasibility-uso-2021-wk1"]
    assert week1["locked"] and week1["paid_requests"] == 7 and week1["credits"] == 70
