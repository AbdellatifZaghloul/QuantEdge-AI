"""DRAFT v2 decision rules: point-in-time invariants on synthetic schedule listings only."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quantedge.core.lineage import LicenceClass
from quantedge.odds.stage import StagePlanError, StageRunStoppedError, run_stage
from quantedge.odds.store import CaptureStore
from quantedge.odds.theoddsapi import OddsApiConfig, TheOddsApiClient
from quantedge.odds.v2_draft import (
    LISTING_SCHEMA,
    apply_validity_censoring,
    day_anchor_manifest,
    day_anchor_population,
    local_instant,
    match_pit_decisions,
    opening_anchor_population,
    resolve_identity,
)
from tests.fixtures.odds_synthetic import ScriptedTransport, historical_body, ok

USO = "tennis_atp_us_open"  # America/New_York; 2022-08-30 is EDT (UTC-4)


def utc(d: int, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    return datetime(2022, 8, d, hh, mm, ss, tzinfo=UTC)


def listings(rows: list[tuple[datetime, str, datetime]], sport: str = USO) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"snapshot_utc": s, "competition_key": sport, "provider_event_id": e, "commence_utc": c}
            for s, e, c in rows
        ],
        schema=LISTING_SCHEMA,
    )


# 10:00 EDT on 08-30 = 14:00 UTC. Night-session starts after 00:00 UTC are still local day 08-30.
BASE = [
    (utc(30, 13, 55), "opener", utc(30, 15)),
    (utc(30, 13, 55), "followed", utc(30, 17)),
    (utc(30, 13, 55), "night", utc(31, 0)),
    (utc(30, 13, 55), "early", utc(30, 14, 30)),  # listed < 60 min after the decision
    (utc(30, 13, 55), "tomorrow", utc(31, 15)),  # local day 08-31
]


def status(frame: pl.DataFrame, event: str) -> str:
    return str(frame.filter(pl.col("provider_event_id") == event)["status"].to_list())


def test_local_instant_handles_dst_offsets() -> None:
    assert local_instant(date(2022, 8, 30), time(10), "America/New_York") == utc(30, 14)
    assert local_instant(date(2022, 1, 18), time(10), "Australia/Melbourne") == datetime(
        2022, 1, 17, 23, tzinfo=UTC
    )


def test_day_anchor_population_uses_only_the_decision_snapshot() -> None:
    pop = day_anchor_population(listings(BASE), sport_key=USO)
    day30 = pop.filter(pl.col("local_day") == date(2022, 8, 30))
    assert set(day30.filter(pl.col("status") == "in_population")["provider_event_id"]) == {
        "opener",
        "followed",
        "night",
    }
    assert status(day30, "early") == "['short_lead']"
    assert "tomorrow" not in day30["provider_event_id"].to_list()
    assert (day30["decision_utc"] == utc(30, 14)).all()
    assert (day30["as_of_snapshot_utc"] == utc(30, 13, 55)).all()
    # No snapshot within 10 minutes of 08-31 14:00 UTC: the day is *not observed* (not "no market").
    day31 = pop.filter(pl.col("local_day") == date(2022, 8, 31))
    assert day31["status"].to_list() == ["not_observed"]
    assert day31["provider_event_id"].is_null().all()


def test_later_revisions_never_move_the_decision_or_population() -> None:
    base = day_anchor_population(listings(BASE), sport_key=USO)
    revised = [
        *BASE,
        (utc(30, 15, 55), "followed", utc(30, 16, 10)),  # earlier estimate published later
        (utc(30, 15, 55), "late_added", utc(30, 18)),  # first listed after the decision
    ]
    after = day_anchor_population(listings(revised), sport_key=USO)
    cols = ["local_day", "decision_utc", "provider_event_id", "as_of_start_utc", "status"]
    assert (
        after.filter(pl.col("local_day") == date(2022, 8, 30))
        .select(cols)
        .sort("provider_event_id")
        .equals(
            base.filter(pl.col("local_day") == date(2022, 8, 30))
            .select(cols)
            .sort("provider_event_id")
        )
    )


def test_first_qualification_keeps_a_postponed_match_on_its_first_day() -> None:
    rows = [*BASE, (utc(31, 13, 55), "followed", utc(31, 16))]  # listed again for 08-31
    pop = day_anchor_population(listings(rows), sport_key=USO)
    assert (
        status(pop.filter(pl.col("local_day") == date(2022, 8, 30)), "followed")
        == "['in_population']"
    )
    assert (
        status(pop.filter(pl.col("local_day") == date(2022, 8, 31)), "followed")
        == "['already_assigned']"
    )


def test_validity_censoring_excludes_only_events_already_started() -> None:
    rows = [*BASE, (utc(30, 16), "opener", utc(30, 13, 50, 12))]  # in-play stamp: began 13:50:12
    pop = apply_validity_censoring(
        day_anchor_population(listings(rows), sport_key=USO), listings(rows)
    )
    day30 = pop.filter(pl.col("local_day") == date(2022, 8, 30))
    assert status(day30, "opener") == "['started_before_decision']"
    assert status(day30, "followed") == "['in_population']"


def test_opening_anchor_uses_the_discovery_snapshot_only() -> None:
    rows = [
        (utc(30, 3, 55), "opener", utc(30, 15)),  # 23:55 EDT on 08-29: before K_d = 00:00 EDT
        (utc(30, 3, 55), "followed", utc(30, 17)),
        (utc(30, 13, 55), "opener", utc(30, 15)),
        (utc(30, 13, 55), "followed", utc(30, 16, 30)),
    ]
    pop = opening_anchor_population(listings(rows), sport_key=USO)
    day30 = pop.filter(pl.col("local_day") == date(2022, 8, 30))
    assert (day30["decision_utc"] == utc(30, 14)).all()  # O_d = 15:00 UTC from discovery, minus 60
    assert set(day30.filter(pl.col("status") == "in_population")["provider_event_id"]) == {
        "opener",
        "followed",
    }
    no_disc = opening_anchor_population(listings(rows[2:]), sport_key=USO)
    assert "no_discovery" in no_disc["status"].to_list()


def test_match_pit_triggers_once_and_ignores_later_revisions() -> None:
    rows = [
        (utc(30, 13, 55), "m", utc(30, 17)),
        (utc(30, 15, 55), "m", utc(30, 16, 46)),  # within 60 min as listed then: decision 15:55
        (utc(30, 16, 15), "m", utc(30, 16, 20)),  # later revision: ignored
        (utc(30, 13, 55), "sparse", utc(30, 20)),  # no poll inside its window
    ]
    out = match_pit_decisions(listings(rows), sport_key=USO)
    m = out.filter(pl.col("provider_event_id") == "m").row(0, named=True)
    assert (m["decision_utc"], m["as_of_start_utc"]) == (utc(30, 15, 55), utc(30, 16, 46))
    assert status(out, "sparse") == "['not_triggered']"


def links_frame(rows: list[tuple[str, str | None, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={"provider_event_id": pl.String, "match_key": pl.String, "link_status": pl.String},
        orient="row",
    )


def test_identity_conflicts_are_excluded_and_never_rescued() -> None:
    rows = [
        (utc(30, 13, 55), "a1", utc(30, 15)),
        (utc(30, 13, 55), "a2", utc(30, 17)),  # concurrent twin of a1 (same match)
        (utc(31, 13, 55), "a2", utc(31, 15)),  # only one twin listed the next day
        (utc(30, 13, 55), "b1", utc(30, 19)),  # store-wide duplicate whose twin is never co-listed
        (utc(31, 13, 55), "b2", utc(31, 19)),
        (utc(30, 13, 55), "c", utc(30, 21)),
        (utc(30, 13, 55), "x", utc(30, 22)),
    ]
    links = links_frame(
        [
            ("a1", "M1", "duplicate_provider_event"),
            ("a2", "M1", "duplicate_provider_event"),
            ("b1", "M2", "duplicate_provider_event"),
            ("b2", "M2", "duplicate_provider_event"),
            ("c", "M3", "linked"),
            ("x", None, "unmatched"),
        ]
    )
    out = resolve_identity(day_anchor_population(listings(rows), sport_key=USO), links)
    got = {
        (r["provider_event_id"], str(r["local_day"])): r["identity"]
        for r in out.iter_rows(named=True)
    }
    assert got[("a1", "2022-08-30")] == got[("a2", "2022-08-30")] == "identity_conflict"
    assert ("a2", "2022-08-31") not in got or got[("a2", "2022-08-31")] == "identity_conflict"
    assert got[("b1", "2022-08-30")] == "unique"
    assert got[("b2", "2022-08-31")] == "already_assigned"
    assert got[("c", "2022-08-30")] == "unique"
    assert got[("x", "2022-08-30")] == "not_linked"


@settings(max_examples=100, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=47),  # snapshot slot (30-min steps over 24 h)
            st.integers(min_value=0, max_value=5),  # event id
            st.integers(
                min_value=-120, max_value=24 * 60
            ),  # listed start offset from snapshot (min)
        ),
        min_size=1,
        max_size=40,
    )
)
def test_day_anchor_point_in_time_invariants(spec: list[tuple[int, int, int]]) -> None:
    start = utc(30, 4)
    rows = []
    for slot, eid, offset in spec:
        snap = start + timedelta(minutes=30 * slot)
        rows.append((snap, f"e{eid}", snap + timedelta(minutes=offset)))
    frame = listings(rows).unique(subset=["snapshot_utc", "provider_event_id"], keep="first")
    pop = day_anchor_population(frame, sport_key=USO)
    inpop = pop.filter(pl.col("status") == "in_population")
    for r in inpop.iter_rows(named=True):
        assert r["as_of_snapshot_utc"] <= r["decision_utc"]  # never a snapshot after the decision
        assert r["as_of_start_utc"] - r["decision_utc"] >= timedelta(minutes=60)
        listed = frame.filter(
            (pl.col("snapshot_utc") == r["as_of_snapshot_utc"])
            & (pl.col("provider_event_id") == r["provider_event_id"])
        )
        assert listed["commence_utc"].to_list() == [r["as_of_start_utc"]]  # as listed *then*
    assert inpop["provider_event_id"].n_unique() == inpop.height  # one decision per event


def test_day_anchor_manifest_is_fixed_in_advance_and_runner_safe(tmp_path: Path) -> None:
    days = [date(2021, 8, 30) + timedelta(days=i) for i in range(7)]
    m = day_anchor_manifest(name="v2-feas-wk1", sport_key=USO, days=days)
    assert m.planned_credits == 70 and len(m.requests) == 7
    assert m.requests[0].request_date_utc == "2021-08-30T14:00:00Z"  # 10:00 EDT
    assert m.requests[0].expected_snapshot_utc == "2021-08-30T13:55:00Z"
    with pytest.raises(StagePlanError, match="split"):
        day_anchor_manifest(name="x", sport_key=USO, days=days * 2)
    store = CaptureStore(tmp_path, "the_odds_api", LicenceClass.COMMERCIAL_LICENSED, "historical")
    dry = run_stage(m, store=store, ledger_path=tmp_path / "l.jsonl")
    assert [r["action"] for r in dry] == ["would_request"] * 7 and dry[-1]["cumulative"] == 70


def test_day_anchor_runner_stops_on_an_old_snapshot(tmp_path: Path) -> None:
    m = day_anchor_manifest(name="v2", sport_key=USO, days=[date(2021, 8, 30), date(2021, 8, 31)])
    store = CaptureStore(tmp_path, "the_odds_api", LicenceClass.COMMERCIAL_LICENSED, "historical")
    old = historical_body(datetime(2021, 8, 30, 13, 45, tzinfo=UTC), [])  # 15 min before T_d
    transport = ScriptedTransport([ok(old, last=10, remaining=1000)])
    client = TheOddsApiClient(
        OddsApiConfig(max_credits_per_run=20, max_retries=0), api_key="k", transport=transport
    )
    with pytest.raises(StageRunStoppedError, match="older than planned"):
        run_stage(m, store=store, ledger_path=tmp_path / "l.jsonl", client=client, execute=True)
    assert len(transport.urls) == 1
