"""Staged historical acquisition: offline planning invariants and the ledger-enforced runner.

Synthetic only: scripted transport, invented events and prices, no network, no credentials.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from typer.testing import CliRunner

from quantedge.cli import app
from quantedge.core.http import HttpResponse
from quantedge.core.lineage import LicenceClass
from quantedge.odds.provider import NormalizedEvent
from quantedge.odds.stage import (
    LedgerEntry,
    StageManifest,
    StagePlanError,
    StageRunStoppedError,
    grid_floor,
    infer_grid_offset,
    manifest_summary,
    plan_stage,
    read_ledger,
    returned_snapshots,
    run_stage,
    stage_audit,
)
from quantedge.odds.store import CaptureStore
from quantedge.odds.tables import build_odds_tables_with_quarantine
from quantedge.odds.theoddsapi import (
    OddsApiConfig,
    OddsApiError,
    TheOddsApiClient,
    TheOddsApiNormalizer,
)
from tests.conftest import REPO_ROOT
from tests.fixtures.odds_synthetic import ScriptedTransport, event, historical_body, ok

SPORT = "tennis_atp_wimbledon"
DISCOVERY = datetime(2022, 6, 28, 11, 55, tzinfo=UTC)


def ev(eid: str, start: datetime, sport: str = SPORT) -> NormalizedEvent:
    return NormalizedEvent(eid, sport, None, f"{eid} One", f"{eid} Two", start)


def at(hh: int, mm: int, day: int = 28) -> datetime:
    return datetime(2022, 6, day, hh, mm, tzinfo=UTC)


EVENTS = [
    ev("e01", at(12, 0)),  # decision 11:00, before the discovery snapshot
    ev("e02", at(12, 10)),  # decision 11:10, before the discovery snapshot
    ev("e03", at(13, 30)),
    ev("e04", at(13, 36)),
    ev("e05", at(13, 43)),
    ev("e06", at(10, 0, day=29)),
    ev("started", at(11, 0)),  # already started at discovery: never planned
    ev("other", at(13, 30), sport="tennis_atp_us_open"),
]


def plan(**kw: object) -> StageManifest:
    args: dict[str, object] = {
        "name": "s",
        "sport_key": SPORT,
        "discovery_capture_id": "cap1_x",
        "discovery_snapshot": DISCOVERY,
        "events": EVENTS,
    }
    return plan_stage(**(args | kw))  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------ planning


def test_grid_floor_and_offset() -> None:
    assert infer_grid_offset(DISCOVERY, 10) == 5
    assert grid_floor(at(11, 0), cadence_minutes=10, offset_minutes=5) == at(10, 55)
    assert grid_floor(at(14, 25), cadence_minutes=10, offset_minutes=5) == at(14, 25)
    assert grid_floor(at(0, 3), cadence_minutes=10, offset_minutes=5) == at(23, 55, day=27)
    with pytest.raises(StagePlanError):
        infer_grid_offset(DISCOVERY + timedelta(seconds=30), 10)


def test_plan_groups_requests_at_earliest_decision() -> None:
    m = plan()
    rows = [(r.request_date_utc, r.expected_snapshot_utc, len(r.events)) for r in m.requests]
    assert rows == [
        ("2022-06-28T11:00:00Z", "2022-06-28T10:55:00Z", 2),
        ("2022-06-28T12:30:00Z", "2022-06-28T12:25:00Z", 2),  # 12:30 and 12:36 (gap 11)
        ("2022-06-28T12:43:00Z", "2022-06-28T12:35:00Z", 1),  # 12:43 is 18 min after 12:25
        ("2022-06-29T09:00:00Z", "2022-06-29T08:55:00Z", 1),
    ]
    assert m.planned_credits == 40 and m.grid_offset_minutes == 5
    planned = {e.provider_event_id for r in m.requests for e in r.events}
    assert planned == {"e01", "e02", "e03", "e04", "e05", "e06"}
    later = {e.provider_event_id for r in m.requests for e in r.events if e.discovered_later}
    assert later == {"e01", "e02"}


def test_plan_never_serves_a_decision_from_a_later_snapshot() -> None:
    for r in plan().requests:
        snap = datetime.fromisoformat(r.expected_snapshot_utc)
        request = datetime.fromisoformat(r.request_date_utc)
        assert snap <= request
        for e in r.events:
            decision = datetime.fromisoformat(e.decision_utc)
            assert request <= decision and decision - snap <= timedelta(minutes=15)


def test_tighter_gap_needs_more_requests() -> None:
    assert len(plan(max_gap_minutes=10).requests) == 5
    with pytest.raises(StagePlanError, match="at least one snapshot interval"):
        plan(max_gap_minutes=5)


def test_budget_ceilings_are_enforced_at_planning() -> None:
    with pytest.raises(StagePlanError, match="approved 130"):
        plan(stage_ceiling=140)
    with pytest.raises(StagePlanError, match="above the stage ceiling"):
        plan(stage_ceiling=30)
    assert plan(regions=("eu", "uk")).planned_credits == 80  # 10 x markets x regions


def test_plan_refuses_to_rebuy_a_stored_snapshot() -> None:
    with pytest.raises(StagePlanError, match="re-buy"):
        plan(already_captured=["2022-06-28T12:25:00Z"])
    assert plan(already_captured=["2022-06-28T11:55:00Z"]).planned_credits == 40


def test_duplicate_event_ids_are_refused() -> None:
    with pytest.raises(StagePlanError, match="duplicate"):
        plan(events=[ev("dup", at(13, 0)), ev("dup", at(14, 0))])


def test_manifest_roundtrip_and_summary_without_event_ids() -> None:
    m = plan()
    assert StageManifest.from_json(m.to_json()) == m
    summary = manifest_summary(m)
    assert [r["cumulative_credits"] for r in summary] == [10, 20, 30, 40]
    assert summary[0]["discovered_later"] == 2 and summary[2]["max_gap_minutes"] == 8.0
    assert "e01" not in json.dumps(summary)


@settings(max_examples=150, deadline=None)
@given(
    st.lists(st.integers(min_value=1, max_value=36 * 60), min_size=1, max_size=13),
    st.sampled_from([10.0, 15.0, 20.0]),
)
def test_plan_invariants_hold_for_any_schedule(offsets: list[int], max_gap: float) -> None:
    # At most 13 events -> at most 13 requests -> always within the 130-credit ceiling.
    events = [ev(f"e{i}", DISCOVERY + timedelta(minutes=m)) for i, m in enumerate(offsets)]
    m = plan(events=events, max_gap_minutes=max_gap)
    seen = [e.provider_event_id for r in m.requests for e in r.events]
    assert sorted(seen) == sorted(e.provider_event_id for e in events)  # each event exactly once
    snaps = [r.expected_snapshot_utc for r in m.requests]
    assert snaps == sorted(set(snaps))  # strictly increasing, no duplicate snapshots
    for r in m.requests:
        snap = datetime.fromisoformat(r.expected_snapshot_utc)
        assert (snap.minute % 10, snap.second) == (5, 0)
        for e in r.events:
            decision = datetime.fromisoformat(e.decision_utc)
            assert snap <= datetime.fromisoformat(r.request_date_utc) <= decision
            assert decision - snap <= timedelta(minutes=max_gap)
            assert e.discovered_later == (decision < DISCOVERY)


# -------------------------------------------------------------------------------------- runner


def store_at(tmp_path: Path) -> CaptureStore:
    return CaptureStore(tmp_path, "the_odds_api", LicenceClass.COMMERCIAL_LICENSED, "historical")


def body_for(snapshot: datetime, ids: list[str]) -> bytes:
    last = snapshot - timedelta(minutes=4)
    return historical_body(
        snapshot,
        [event(i, "A", "B", snapshot + timedelta(hours=2), [("bk", last, 1.8, 2.1)]) for i in ids],
    )


def paid_client(
    responses: list[HttpResponse], *, budget: int = 40, retries: int = 0
) -> tuple[TheOddsApiClient, ScriptedTransport]:
    transport = ScriptedTransport(responses)
    config = OddsApiConfig(max_credits_per_run=budget, max_retries=retries)
    return TheOddsApiClient(
        config, api_key="k", transport=transport, sleep=lambda _: None
    ), transport


def scripted_ok(m: StageManifest, *, remaining: int = 1000) -> list[HttpResponse]:
    out = []
    for r in m.requests:
        remaining -= 10
        snap = datetime.fromisoformat(r.expected_snapshot_utc)
        ids = [e.provider_event_id for e in r.events]
        out.append(ok(body_for(snap, ids), last=10, remaining=remaining))
    return out


def test_dry_run_makes_no_calls_and_writes_no_ledger(tmp_path: Path) -> None:
    m = plan()
    client, transport = paid_client([])
    report = run_stage(m, store=store_at(tmp_path), ledger_path=tmp_path / "l.jsonl", client=client)
    assert [r["action"] for r in report] == ["would_request"] * 4
    assert [r["cumulative"] for r in report] == [10, 20, 30, 40]
    assert transport.urls == [] and not (tmp_path / "l.jsonl").exists()


def test_execute_writes_ledger_and_resume_skips_everything(tmp_path: Path) -> None:
    m, store, ledger = plan(), store_at(tmp_path), tmp_path / "l.jsonl"
    client, transport = paid_client(scripted_ok(m))
    report = run_stage(m, store=store, ledger_path=ledger, client=client, execute=True)
    assert [r["action"] for r in report] == ["requested"] * 4 and len(transport.urls) == 4
    entries = read_ledger(ledger)
    assert [e.cumulative_credits for e in entries] == [10, 20, 30, 40]
    assert [e.credits_remaining for e in entries] == [990, 980, 970, 960]
    assert all(e.events_present == e.events_planned for e in entries)
    assert sorted(returned_snapshots(store, SPORT)) == [r.expected_snapshot_utc for r in m.requests]
    assert all("apiKey" not in json.dumps(e.__dict__) for e in entries)
    again, transport2 = paid_client([])
    report2 = run_stage(m, store=store, ledger_path=ledger, client=again, execute=True)
    assert [r["action"] for r in report2] == ["skip_already_captured"] * 4
    assert transport2.urls == []


def test_snapshot_already_in_store_is_never_rebought(tmp_path: Path) -> None:
    m, store = plan(), store_at(tmp_path)
    first = m.requests[0]
    seed, _ = paid_client(
        [
            ok(
                body_for(datetime.fromisoformat(first.expected_snapshot_utc), []),
                last=10,
                remaining=990,
            )
        ]
    )
    store.append(
        seed.historical_odds(SPORT, datetime.fromisoformat(first.request_date_utc), approved=True)
    )
    client, transport = paid_client(scripted_ok(m)[1:])
    report = run_stage(
        m, store=store, ledger_path=tmp_path / "l.jsonl", client=client, execute=True
    )
    assert report[0]["action"] == "skip_already_captured" and len(transport.urls) == 3


def test_missing_event_is_recorded_not_hidden(tmp_path: Path) -> None:
    m = plan()
    responses = scripted_ok(m)
    responses[0] = ok(body_for(at(10, 55), ["e02"]), last=10, remaining=990)  # e01 absent
    client, _ = paid_client(responses)
    run_stage(
        m, store=store_at(tmp_path), ledger_path=tmp_path / "l.jsonl", client=client, execute=True
    )
    first = read_ledger(tmp_path / "l.jsonl")[0]
    assert (first.events_planned, first.events_present) == (2, 1)


def test_unexpected_charge_stops_after_storing_the_body(tmp_path: Path) -> None:
    m, store, ledger = plan(), store_at(tmp_path), tmp_path / "l.jsonl"
    responses = scripted_ok(m)
    responses[0] = ok(body_for(at(10, 55), ["e01", "e02"]), last=20, remaining=980)
    client, transport = paid_client(responses)
    with pytest.raises(StageRunStoppedError, match="charged 20"):
        run_stage(m, store=store, ledger_path=ledger, client=client, execute=True)
    assert len(transport.urls) == 1 and len(store.records()) == 1
    assert read_ledger(ledger)[0].cumulative_credits == 20


def test_first_call_is_reconciled_against_the_last_stored_quota(tmp_path: Path) -> None:
    m, store = plan(), store_at(tmp_path)
    seed, _ = paid_client([ok(body_for(DISCOVERY, []), last=10, remaining=1500)])
    store.append(seed.historical_odds(SPORT, at(12, 0), approved=True))  # e.g. the discovery call
    client, transport = paid_client(scripted_ok(m))  # first paid call reports 990, not 1490
    with pytest.raises(StageRunStoppedError, match="quota moved from 1500 to 990"):
        run_stage(m, store=store, ledger_path=tmp_path / "l.jsonl", client=client, execute=True)
    assert len(transport.urls) == 1


def test_quota_drift_stops_the_run(tmp_path: Path) -> None:
    m = plan()
    responses = scripted_ok(m)
    responses[1] = ok(body_for(at(12, 25), ["e03", "e04"]), last=10, remaining=900)
    client, transport = paid_client(responses)
    with pytest.raises(StageRunStoppedError, match="quota moved"):
        run_stage(
            m,
            store=store_at(tmp_path),
            ledger_path=tmp_path / "l.jsonl",
            client=client,
            execute=True,
        )
    assert len(transport.urls) == 2


def test_older_than_planned_snapshot_stops_the_run(tmp_path: Path) -> None:
    m = plan()
    responses = scripted_ok(m)
    responses[0] = ok(body_for(at(10, 45), ["e01", "e02"]), last=10, remaining=990)
    client, transport = paid_client(responses)
    with pytest.raises(StageRunStoppedError, match="grid assumption failed"):
        run_stage(
            m,
            store=store_at(tmp_path),
            ledger_path=tmp_path / "l.jsonl",
            client=client,
            execute=True,
        )
    assert len(transport.urls) == 1


def test_post_request_snapshot_stops_the_run(tmp_path: Path) -> None:
    m = plan()
    responses = scripted_ok(m)
    responses[0] = ok(body_for(at(11, 5), ["e01", "e02"]), last=10, remaining=990)
    client, _ = paid_client(responses)
    with pytest.raises(StageRunStoppedError, match="post-request"):
        run_stage(
            m,
            store=store_at(tmp_path),
            ledger_path=tmp_path / "l.jsonl",
            client=client,
            execute=True,
        )


def test_cumulative_ledger_caps_spend_across_runs(tmp_path: Path) -> None:
    m, ledger = plan(), tmp_path / "l.jsonl"
    # 30 credits already recorded for this stage by an earlier run: only one more call fits the 40.
    prior = {
        "request_id": "earlier-run",
        "request_date_utc": "2022-06-28T10:00:00Z",
        "expected_snapshot_utc": "2022-06-28T09:55:00Z",
        "returned_snapshot_utc": "2022-06-28T09:55:00Z",
        "credits_charged": 30,
        "credits_remaining": None,
        "cumulative_credits": 30,
        "capture_id": "cap1_prior",
        "events_planned": 0,
        "events_present": 0,
        "recorded_at_utc": "2026-11-02T00:00:00Z",
    }
    ledger.write_text(json.dumps(prior) + "\n", encoding="utf-8")
    client, transport = paid_client(scripted_ok(m))
    with pytest.raises(StageRunStoppedError, match="would reach 50"):
        run_stage(m, store=store_at(tmp_path), ledger_path=ledger, client=client, execute=True)
    assert len(transport.urls) == 1 and read_ledger(ledger)[-1].cumulative_credits == 40


def test_low_provider_quota_blocks_before_calling(tmp_path: Path) -> None:
    m = plan()
    responses = scripted_ok(m, remaining=15)  # 5 left after the first call
    client, transport = paid_client(responses)
    with pytest.raises(StageRunStoppedError, match="provider quota 5"):
        run_stage(
            m,
            store=store_at(tmp_path),
            ledger_path=tmp_path / "l.jsonl",
            client=client,
            execute=True,
        )
    assert len(transport.urls) == 1


@pytest.mark.parametrize(
    ("budget", "retries", "match"),
    [(40, 4, "max_retries=0"), (50, 0, "run budget exceeds")],
)
def test_unsafe_client_configuration_is_refused(
    tmp_path: Path, budget: int, retries: int, match: str
) -> None:
    client, transport = paid_client([], budget=budget, retries=retries)
    with pytest.raises(StageRunStoppedError, match=match):
        run_stage(
            plan(),
            store=store_at(tmp_path),
            ledger_path=tmp_path / "l.jsonl",
            client=client,
            execute=True,
        )
    assert transport.urls == []


def test_market_region_mismatch_is_refused(tmp_path: Path) -> None:
    transport = ScriptedTransport([])
    config = OddsApiConfig(max_credits_per_run=40, max_retries=0, regions=("uk",))
    client = TheOddsApiClient(config, api_key="k", transport=transport)
    with pytest.raises(StageRunStoppedError, match="markets/regions"):
        run_stage(
            plan(),
            store=store_at(tmp_path),
            ledger_path=tmp_path / "l.jsonl",
            client=client,
            execute=True,
        )


def test_non_200_is_not_retried_and_nothing_is_stored(tmp_path: Path) -> None:
    m, store = plan(), store_at(tmp_path)
    client, transport = paid_client([HttpResponse(500, {}, b"boom")])
    with pytest.raises(OddsApiError):
        run_stage(m, store=store, ledger_path=tmp_path / "l.jsonl", client=client, execute=True)
    assert len(transport.urls) == 1 and store.records() == []
    assert not (tmp_path / "l.jsonl").exists()


# ------------------------------------------------------------------------------------------ CLI

runner = CliRunner()


def test_cli_plan_and_dry_run_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QE_ODDS_API_KEY", raising=False)
    discovery = historical_body(
        DISCOVERY,
        [
            event("e01", "A", "B", at(12, 0), [("bk", DISCOVERY, 1.8, 2.1)]),
            event("e03", "C", "D", at(13, 30), [("bk", DISCOVERY, 1.8, 2.1)]),
        ],
    )
    seed, _ = paid_client([ok(discovery, last=10)])
    record, _ = store_at(tmp_path).append(seed.historical_odds(SPORT, at(12, 0), approved=True))
    common = ["--config-dir", str(REPO_ROOT / "configs"), "--data-root", str(tmp_path)]
    planned = runner.invoke(
        app,
        ["odds", "stage-plan", "--name", "t", "--discovery-capture-id", record.capture_id, *common],
    )
    assert planned.exit_code == 0, planned.output
    out = json.loads(planned.output)
    assert (out["requests"], out["planned_credits"], out["events"]) == (2, 20, 2)
    assert "e01" not in planned.output
    dry = runner.invoke(app, ["odds", "stage-run", "--name", "t", *common])
    assert dry.exit_code == 0 and json.loads(dry.output)["execute"] is False
    refused = runner.invoke(app, ["odds", "stage-run", "--name", "t", "--execute", *common])
    assert refused.exit_code == 2
    assert not (tmp_path / "processed/odds/the_odds_api/stages/t/ledger.jsonl").exists()


# ------------------------------------------------------------------------------- go / no-go


def stage_body(
    snapshot: datetime,
    starts: dict[str, datetime],
    *,
    lag_minutes: int = 4,
    books: int = 2,
    skip: frozenset[str] = frozenset(),
) -> bytes:
    last = snapshot - timedelta(minutes=lag_minutes)
    data = [
        event(i, f"{i} One", f"{i} Two", s, [(f"bk{b}", last, 1.8, 2.1) for b in range(books)])
        for i, s in starts.items()
        if i not in skip
    ]
    return historical_body(snapshot, data)


def run_synthetic_stage(
    tmp_path: Path, bodies: list[bytes]
) -> tuple[StageManifest, CaptureStore, list[LedgerEntry]]:
    m, store, ledger = plan(), store_at(tmp_path), tmp_path / "l.jsonl"
    responses = [ok(b, last=10, remaining=1000 - 10 * (i + 1)) for i, b in enumerate(bodies)]
    client, _ = paid_client(responses)
    run_stage(m, store=store, ledger_path=ledger, client=client, execute=True)
    return m, store, read_ledger(ledger)


STARTS = {e.provider_event_id: e.commence_time_utc for e in EVENTS[:6]}


def request_starts(m: StageManifest, i: int) -> dict[str, datetime]:
    return {e.provider_event_id: STARTS[e.provider_event_id] for e in m.requests[i].events}


def audit(tmp_path: Path, bodies: list[bytes], *, linked: bool = True) -> dict[str, Any]:
    m, store, ledger = run_synthetic_stage(tmp_path, bodies)
    built = build_odds_tables_with_quarantine(store, TheOddsApiNormalizer())  # type: ignore[arg-type]
    links = (
        pl.DataFrame({"event_id": built.events["event_id"], "status": "linked"}) if linked else None
    )
    return stage_audit(
        m,
        events=built.events,
        quotes=built.quotes,
        quarantine=built.quarantine,
        ledger=ledger,
        links=links,
    )


def snaps(m: StageManifest) -> list[datetime]:
    return [datetime.fromisoformat(r.expected_snapshot_utc) for r in m.requests]


def test_clean_stage_passes_every_criterion(tmp_path: Path) -> None:
    m = plan()
    bodies = [stage_body(s, request_starts(m, i)) for i, s in enumerate(snaps(m))]
    result = audit(tmp_path, bodies)
    assert result["go"] is True and all(result["checks"].values())
    metrics = result["metrics"]
    assert metrics["share_events_ge2_available"] == 1.0 and metrics["credits_spent"] == 40
    assert metrics["discovered_later"] == {"planned": 2, "ge1_available": 2}
    # Gap (decision - snapshot) + 4 min source lag: e.g. 5 + 4 = 9 min.
    assert 4.0 <= metrics["median_quote_age_minutes"] <= 19.0


def test_linking_is_required_for_go(tmp_path: Path) -> None:
    m = plan()
    bodies = [stage_body(s, request_starts(m, i)) for i, s in enumerate(snaps(m))]
    result = audit(tmp_path, bodies, linked=False)
    assert result["checks"]["c5_linking"] is False and result["go"] is False


def test_discovered_later_event_missing_from_its_snapshot_counts_as_failure(
    tmp_path: Path,
) -> None:
    m = plan()
    bodies = [stage_body(s, request_starts(m, i)) for i, s in enumerate(snaps(m))]
    bodies[0] = stage_body(snaps(m)[0], request_starts(m, 0), skip=frozenset({"e01", "e02"}))
    metrics = audit(tmp_path, bodies)["metrics"]
    assert metrics["planned_events"] == 6 and metrics["events_seen"] == 4
    assert metrics["discovered_later"] == {"planned": 2, "ge1_available": 0}
    assert metrics["share_events_ge1_available"] == 4 / 6


def test_earlier_reschedule_moves_the_frozen_anchor_and_excludes(tmp_path: Path) -> None:
    m = plan()
    bodies = [stage_body(s, request_starts(m, i)) for i, s in enumerate(snaps(m))]
    moved = request_starts(m, 1) | {"e03": STARTS["e03"] - timedelta(minutes=20)}
    bodies[1] = stage_body(snaps(m)[1], moved)  # 12:25 snapshot shows e03 at 13:10 (was 13:30)
    metrics = audit(tmp_path, bodies)["metrics"]
    # New decision 12:10 precedes the 12:25 snapshot: no admissible quote, conservatively excluded.
    assert metrics["anchor_moved_events"] == 1 and metrics["share_events_ge1_available"] == 5 / 6


def test_lagging_markets_fail_freshness(tmp_path: Path) -> None:
    m = plan()
    bodies = [stage_body(s, request_starts(m, i), lag_minutes=25) for i, s in enumerate(snaps(m))]
    result = audit(tmp_path, bodies)
    assert result["checks"]["c1_ge1_available"] is False
    assert result["checks"]["c3_median_age"] is False and result["go"] is False
