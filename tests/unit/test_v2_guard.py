"""Programme-level V2 acquisition guard: every unsafe paid call is refused before any request.

Synthetic only: scripted transport, invented events, temporary stores; no network, no outcomes.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quantedge.cli import app
from quantedge.core.http import HttpResponse
from quantedge.core.lineage import LicenceClass
from quantedge.odds import commands
from quantedge.odds import v2_guard as guard
from quantedge.odds.provider import NormalizedEvent
from quantedge.odds.stage import StageManifest, plan_stage, read_ledger, run_stage
from quantedge.odds.store import CaptureStore
from quantedge.odds.theoddsapi import OddsApiConfig, TheOddsApiClient
from quantedge.odds.v2_draft import (
    day_anchor_manifest,
    load_calendar,
    write_lock,
    write_tranche_plan,
)
from quantedge.odds.v2_guard import (
    V2GuardError,
    guarded_stage_run,
    guarded_tranche_run,
    preflight,
    v2_window_conflict,
    verify_frozen_v2,
)
from tests.conftest import REPO_ROOT
from tests.fixtures.odds_synthetic import ScriptedTransport, historical_body, ok

CAL = load_calendar()["uso-2021"]
DAYS = list(CAL.planned_days())
AFTER_FREEZE = datetime(2026, 11, 4, 12, tzinfo=UTC)
BEFORE_FREEZE = datetime(2026, 11, 1, 12, tzinfo=UTC)


def store_at(root: Path) -> CaptureStore:
    return CaptureStore(root, "the_odds_api", LicenceClass.COMMERCIAL_LICENSED, "historical")


def client_for(
    responses: list[HttpResponse], budget: int, clock: datetime = AFTER_FREEZE
) -> tuple[TheOddsApiClient, ScriptedTransport]:
    transport = ScriptedTransport(responses)
    config = OddsApiConfig(max_credits_per_run=budget, max_retries=0)
    client = TheOddsApiClient(config, api_key="k", transport=transport, clock=lambda: clock)
    return client, transport


def body(d: date) -> bytes:
    return historical_body(CAL.decision_utc(d) - timedelta(minutes=5), [])


def make_stage(root: Path, name: str, days: list[date]) -> Path:
    stage = root / "stages" / name
    stage.mkdir(parents=True)
    m = day_anchor_manifest(name=name, calendar=CAL, days=days)
    (stage / "manifest.json").write_text(m.to_json(), encoding="utf-8")
    return stage


def pay(stage: Path, store: CaptureStore, days: list[date], start: int = 1000) -> None:
    """Pay the first ``len(days)`` requests of the stage's manifest (synthetic transport)."""
    m = StageManifest.from_json((stage / "manifest.json").read_text(encoding="utf-8"))
    rs = [ok(body(d), last=10, remaining=start - 10 * (i + 1)) for i, d in enumerate(days)]
    client, _ = client_for(rs, 10 * len(days))
    run_stage(
        m, store=store, ledger_path=stage / "ledger.jsonl", client=client, execute=True,
        allowed_request_ids=frozenset(r.request_id for r in m.requests[: len(days)]),
        credit_cap=10 * len(days),
    )  # fmt: skip


# ------------------------------------------------------------------------ frozen fingerprints


def copy_repo(tmp: Path) -> Path:
    for rel in (
        "configs/preregistration/market_benchmark_v2.yaml",
        "src/quantedge/reference/v2_draft_edition_calendar.csv",
        "src/quantedge/odds/v2_draft.py",
        "src/quantedge/odds/stage.py",
    ):
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, tmp / rel)
    return tmp


def test_frozen_fingerprints_verify_on_the_repository() -> None:
    protocol = verify_frozen_v2(REPO_ROOT)
    assert protocol["status"] == "FROZEN"
    assert protocol["acquisition"]["programme_ceiling_credits"] == 1430


@pytest.mark.parametrize(
    ("rel", "match"),
    [
        ("configs/preregistration/market_benchmark_v2.yaml", "fingerprint mismatch"),
        ("src/quantedge/reference/v2_draft_edition_calendar.csv", "calendar fingerprint"),
        ("src/quantedge/odds/v2_draft.py", "frozen implementation changed"),
        ("src/quantedge/odds/stage.py", "frozen implementation changed"),
    ],
)
def test_any_change_to_a_frozen_artifact_is_refused(tmp_path: Path, rel: str, match: str) -> None:
    root = copy_repo(tmp_path)
    with (root / rel).open("a", encoding="utf-8") as fh:
        fh.write("\n# tampered\n")
    with pytest.raises(V2GuardError, match=match):
        verify_frozen_v2(root)


# ------------------------------------------------------------------- programme-wide accounting


def test_spending_is_counted_across_stages_against_the_ceiling(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    a = make_stage(tmp_path, "a", DAYS[:2])
    pay(a, store, DAYS[:2])
    b = make_stage(tmp_path, "b", DAYS[2:4])
    ok_summary = preflight(b, repo_root=REPO_ROOT, store=store, ceiling=40)
    assert (ok_summary["spent_before"], ok_summary["this_call_max"]) == (20, 20)
    assert ok_summary["remaining_after"] == 0
    with pytest.raises(V2GuardError, match=r"spent 20 \+ this call 20 > 30"):
        preflight(b, repo_root=REPO_ROOT, store=store, ceiling=30)


def test_refusal_happens_before_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = store_at(tmp_path)
    a = make_stage(tmp_path, "a", DAYS[:2])
    pay(a, store, DAYS[:2])
    b = make_stage(tmp_path, "b", DAYS[2:4])
    client, transport = client_for([ok(body(DAYS[2]), last=10)], 20)

    real = guard.preflight
    monkeypatch.setattr(guard, "preflight", lambda *a, **k: real(*a, **k, ceiling=30))
    with pytest.raises(V2GuardError, match="programme ceiling"):
        guarded_stage_run(b, repo_root=REPO_ROOT, store=store, client=client, execute=True)
    assert transport.urls == [] and not (b / "ledger.jsonl").exists()


def test_tranche_runs_are_guarded_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = store_at(tmp_path)
    a = make_stage(tmp_path, "a", DAYS[:2])
    pay(a, store, DAYS[:2])
    b = make_stage(tmp_path, "b", DAYS[2:9])
    write_tranche_plan(b, sizes=[2, 5], max_credits=[20, 50])

    real = guard.preflight
    monkeypatch.setattr(guard, "preflight", lambda *a, **k: real(*a, **k, ceiling=35))
    client, transport = client_for([], 20)
    with pytest.raises(V2GuardError, match="programme ceiling"):
        guarded_tranche_run(b, 1, repo_root=REPO_ROOT, store=store, client=client, execute=True)
    assert transport.urls == []


# ----------------------------------------------------------------- overlaps and consistency


def test_a_day_claimed_by_another_stage_is_refused(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    make_stage(tmp_path, "a", DAYS[:2])
    b = make_stage(tmp_path, "b", DAYS[1:3])  # overlaps a on DAYS[1]
    with pytest.raises(V2GuardError, match="already claimed or paid"):
        preflight(b, repo_root=REPO_ROOT, store=store)


def test_an_off_calendar_manifest_anywhere_blocks_paid_calls(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    b = make_stage(tmp_path, "b", DAYS[:1])
    evil = make_stage(tmp_path, "evil", DAYS[5:6])
    raw = json.loads((evil / "manifest.json").read_text(encoding="utf-8"))
    raw["requests"][0]["request_date_utc"] = "2021-09-04T20:00:00Z"
    (evil / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(V2GuardError, match="does not match the frozen calendar"):
        preflight(b, repo_root=REPO_ROOT, store=store)


@pytest.mark.parametrize("target", ["ledger.jsonl", "manifest.json"])
def test_a_changed_locked_stage_blocks_paid_calls(tmp_path: Path, target: str) -> None:
    store = store_at(tmp_path)
    a = make_stage(tmp_path, "a", DAYS[:2])
    pay(a, store, DAYS[:2])
    write_lock(a, store)
    b = make_stage(tmp_path, "b", DAYS[2:3])
    assert preflight(b, repo_root=REPO_ROOT, store=store)["spent_before"] == 20
    with (a / target).open("a", encoding="utf-8") as fh:
        fh.write(" ")
    with pytest.raises(V2GuardError, match="changed after the lock"):
        preflight(b, repo_root=REPO_ROOT, store=store)


def test_ledger_must_match_the_store(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    a = make_stage(tmp_path, "a", DAYS[:1])
    pay(a, store, DAYS[:1])
    ledger = a / "ledger.jsonl"
    ledger.write_text(
        ledger.read_text(encoding="utf-8").replace('"credits_charged": 10', '"credits_charged": 5'),
        encoding="utf-8",
    )
    b = make_stage(tmp_path, "b", DAYS[1:2])
    with pytest.raises(V2GuardError, match="does not match the store"):
        preflight(b, repo_root=REPO_ROOT, store=store)


def test_unaccounted_post_freeze_capture_blocks_paid_calls(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    b = make_stage(tmp_path, "b", DAYS[:1])
    rogue, _ = client_for([ok(body(DAYS[3]), last=10)], 10, clock=AFTER_FREEZE)
    store.append(rogue.historical_odds(CAL.sport_key, CAL.decision_utc(DAYS[3]), approved=True))
    with pytest.raises(V2GuardError, match="after the freeze have no ledger"):
        preflight(b, repo_root=REPO_ROOT, store=store)


def test_pre_freeze_design_captures_are_allowed(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    b = make_stage(tmp_path, "b", DAYS[:1])
    old, _ = client_for([ok(body(DAYS[3]), last=10)], 10, clock=BEFORE_FREEZE)
    store.append(old.historical_odds(CAL.sport_key, CAL.decision_utc(DAYS[3]), approved=True))
    assert preflight(b, repo_root=REPO_ROOT, store=store)["spent_before"] == 0


def test_already_paid_requests_cost_nothing_on_resume(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    a = make_stage(tmp_path, "a", DAYS[:3])
    pay(a, store, DAYS[:1])
    summary = preflight(a, repo_root=REPO_ROOT, store=store, ceiling=30)
    assert (summary["spent_before"], summary["this_call_max"]) == (10, 20)
    assert [e.request_id for e in read_ledger(a / "ledger.jsonl")] == ["a-01"]


# ------------------------------------------------------------------------------- CLI paths


def test_window_conflict_bounds() -> None:
    first, last = CAL.first_day, CAL.last_day
    assert v2_window_conflict(CAL.sport_key, first - timedelta(days=2)) == "uso-2021"
    assert v2_window_conflict(CAL.sport_key, first - timedelta(days=3)) is None
    assert v2_window_conflict(CAL.sport_key, last + timedelta(days=2)) == "uso-2021"
    assert v2_window_conflict("tennis_atp_wimbledon", first) is None


class _NoClientError(AssertionError):
    pass


def _forbid_client(*_: object, **__: object) -> None:
    raise _NoClientError("a client was created")


def test_capture_historical_in_a_v2_window_is_refused_before_any_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands, "TheOddsApiClient", _forbid_client)
    args = ["odds", "capture-historical", "--sport", "tennis_atp_us_open", "--approve-paid-call",
            "--max-credits", "10", "--data-root", str(tmp_path),
            "--config-dir", str(REPO_ROOT / "configs")]  # fmt: skip
    refused = CliRunner().invoke(app, [*args, "--at", "2021-09-06T13:59:00Z"])
    assert refused.exit_code == 2 and "frozen V2 window of uso-2021" in refused.output
    outside = CliRunner().invoke(app, [*args, "--at", "2021-10-20T12:00:00Z"])
    assert isinstance(outside.exception, _NoClientError)  # outside the window it would proceed


def test_paid_v1_stage_execution_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands, "TheOddsApiClient", _forbid_client)
    m = plan_stage(
        name="v1", sport_key="tennis_atp_us_open", discovery_capture_id="c",
        discovery_snapshot=datetime(2022, 8, 30, 11, 55, tzinfo=UTC),
        events=[NormalizedEvent("e", "tennis_atp_us_open", None, "a", "b",
                                datetime(2022, 8, 30, 16, tzinfo=UTC))],
    )  # fmt: skip
    stage = tmp_path / "processed" / "odds" / "the_odds_api" / "stages" / "v1"
    stage.mkdir(parents=True)
    (stage / "manifest.json").write_text(m.to_json(), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        ["odds", "stage-run", "--name", "v1", "--execute", "--approve-paid-calls",
         "--data-root", str(tmp_path), "--config-dir", str(REPO_ROOT / "configs")],
    )  # fmt: skip
    assert result.exit_code == 2 and "suspended (D-34)" in result.output


# Every paid call path must stay behind a guard. The frozen modules cannot be edited, so new call
# sites anywhere else are caught statically. (commands.py: capture-historical is window-guarded;
# its stage-run call without a client is the v1 dry run only.)
ALLOWED_PAID_CALL_SITES = {
    ("odds/commands.py", ".historical_odds("): 1,
    ("odds/commands.py", "run_stage("): 1,
    ("odds/stage.py", ".historical_odds("): 1,
    ("odds/v2_draft.py", "run_stage("): 1,
    ("odds/v2_guard.py", "run_stage("): 1,
    ("odds/v2_guard.py", "run_tranche("): 1,
}


def test_no_unguarded_paid_call_sites() -> None:
    src = REPO_ROOT / "src" / "quantedge"
    found: dict[tuple[str, str], int] = {}
    for path in src.rglob("*.py"):
        rel = path.relative_to(src).as_posix()
        for line in path.read_text(encoding="utf-8").splitlines():
            code = line.split("#", 1)[0]
            for token in (".historical_odds(", "run_stage(", "run_tranche("):
                if token in code and "def " not in code:
                    found[(rel, token)] = found.get((rel, token), 0) + 1
    assert found == ALLOWED_PAID_CALL_SITES
