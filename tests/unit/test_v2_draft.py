"""DRAFT v2 (A2) rules: point-in-time, calendar, identity, runner and audit invariants.

Synthetic only: invented events and prices, scripted transport, no network, no outcomes.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quantedge.core.http import HttpResponse
from quantedge.core.lineage import LicenceClass
from quantedge.odds.provider import NormalizedEvent
from quantedge.odds.stage import (
    LOCK_FILE,
    StageManifest,
    StagePlanError,
    StageRunStoppedError,
    plan_stage,
    read_ledger,
    run_stage,
    stage_audit,
)
from quantedge.odds.store import CaptureStore
from quantedge.odds.tables import build_odds_tables_with_quarantine
from quantedge.odds.theoddsapi import OddsApiConfig, TheOddsApiClient, TheOddsApiNormalizer
from quantedge.odds.v2_draft import (
    LISTING_SCHEMA,
    EditionCalendar,
    V2IntegrationError,
    apply_validity_censoring,
    as_of_snapshot,
    day_anchor_manifest,
    day_anchor_population,
    identity_links,
    load_calendar,
    local_instant,
    manifest_days,
    match_pit_decisions,
    opening_anchor_population,
    resolve_identity,
    schedule_listings,
    v2_audit,
    write_lock,
)
from tests.fixtures.odds_synthetic import ScriptedTransport, event, historical_body, ok

USO = "tennis_atp_us_open"  # America/New_York; late August is EDT (UTC-4): 10:00 local = 14:00Z
CAL = EditionCalendar(
    edition_id="syn-uso",
    sport_key=USO,
    season=2022,
    canonical_tournament_id="t-syn",
    time_zone="America/New_York",
    first_day=date(2022, 8, 30),
    last_day=date(2022, 9, 1),
    rest_days=(date(2022, 9, 1),),
    expected_empty_days=(),
    source="synthetic",
    verified=False,
)


def utc(d: int, hh: int, mm: int = 0, ss: int = 0, month: int = 8) -> datetime:
    return datetime(2022, month, d, hh, mm, ss, tzinfo=UTC)


def listings(rows: list[tuple[datetime, str, datetime]], sport: str = USO) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"snapshot_utc": s, "competition_key": sport, "provider_event_id": e, "commence_utc": c}
            for s, e, c in rows
        ],
        schema=LISTING_SCHEMA,
    )


BASE = [
    (utc(30, 13, 55), "opener", utc(30, 15)),
    (utc(30, 13, 55), "followed", utc(30, 17)),
    (utc(30, 13, 55), "night", utc(31, 0)),  # 20:00 EDT: still local day 08-30
    (utc(30, 13, 55), "early", utc(30, 14, 30)),  # < 60 min after the decision
    (utc(30, 13, 55), "tomorrow", utc(31, 15)),  # local day 08-31
]


def statuses(frame: pl.DataFrame, event: str) -> list[str]:
    return frame.filter(pl.col("provider_event_id") == event)["status"].to_list()


def day(frame: pl.DataFrame, d: date) -> pl.DataFrame:
    return frame.filter(pl.col("local_day") == d)


# ----------------------------------------------------------------------------------- calendar


def test_local_instant_handles_dst_offsets() -> None:
    assert local_instant(date(2022, 8, 30), CAL_TIME, "America/New_York") == utc(30, 14)
    assert local_instant(date(2022, 1, 18), CAL_TIME, "Australia/Melbourne") == datetime(
        2022, 1, 17, 23, tzinfo=UTC
    )


CAL_TIME = time(10, 0)


def test_frozen_calendar_covers_ten_editions_and_143_days() -> None:
    cal = load_calendar()
    assert len(cal) == 10 and not any(c.verified for c in cal.values())
    assert sum(len(c.planned_days()) for c in cal.values()) == 143
    uso21 = cal["uso-2021"]
    assert uso21.planned_days()[0] == date(2021, 8, 30) and len(uso21.planned_days()) == 14
    assert uso21.canonical_tournament_id == "sackmann:2021-560"
    assert all(cal[e].first_day.weekday() == 6 for e in ("rg-2020", "rg-2021", "rg-2022"))
    assert all(c.last_day.weekday() == 6 for c in cal.values())  # finals on Sunday
    wim21 = cal["wim-2021"]
    assert wim21.day_kind(date(2021, 7, 4)) == "rest_day"
    assert date(2021, 7, 4) in wim21.planned_days()  # rest days are still requested


# ------------------------------------------------------------------------------- population


def test_day_anchor_population_uses_only_the_decision_snapshot() -> None:
    pop = day_anchor_population(listings(BASE), calendar=CAL)
    d30 = day(pop, date(2022, 8, 30))
    inpop = set(d30.filter(pl.col("status") == "in_population")["provider_event_id"])
    assert inpop == {"opener", "followed", "night"}
    assert statuses(d30, "early") == ["short_lead"]
    assert "tomorrow" not in d30["provider_event_id"].to_list()
    assert (d30["decision_utc"] == utc(30, 14)).all()
    assert (d30["as_of_snapshot_utc"] == utc(30, 13, 55)).all()
    d31 = day(pop, date(2022, 8, 31))  # no snapshot near 08-31 14:00Z: not observed
    assert d31["status"].to_list() == ["snapshot_missing"]
    rest = day(pop, date(2022, 9, 1))
    assert rest["day_kind"].to_list() == ["rest_day"]
    assert rest["status"].to_list() == ["snapshot_missing"]
    with pytest.raises(V2IntegrationError, match="outside the frozen calendar"):
        day_anchor_population(listings(BASE), calendar=CAL, days=[date(2022, 9, 2)])


def test_empty_rest_day_is_reported_not_skipped() -> None:
    rows = [*BASE, (utc(1, 13, 55, month=9), "next_week", utc(5, 15, month=9))]
    rest = day(day_anchor_population(listings(rows), calendar=CAL), date(2022, 9, 1))
    assert rest["status"].to_list() == ["no_listing"]


def test_later_revisions_never_move_the_decision_or_population() -> None:
    cols = ["local_day", "decision_utc", "provider_event_id", "as_of_start_utc", "status"]
    base = day(day_anchor_population(listings(BASE), calendar=CAL), date(2022, 8, 30))
    revised = [
        *BASE,
        (utc(30, 15, 55), "followed", utc(30, 16, 10)),  # earlier estimate published later
        (utc(30, 15, 55), "late_added", utc(30, 18)),  # first listed after the decision
    ]
    after = day(day_anchor_population(listings(revised), calendar=CAL), date(2022, 8, 30))
    assert (
        after.select(cols)
        .sort("provider_event_id")
        .equals(base.select(cols).sort("provider_event_id"))
    )


def test_first_qualification_keeps_a_postponed_match_on_its_first_day() -> None:
    rows = [*BASE, (utc(31, 13, 55), "followed", utc(31, 16))]  # re-listed for 08-31
    pop = day_anchor_population(listings(rows), calendar=CAL)
    assert statuses(day(pop, date(2022, 8, 30)), "followed") == ["in_population"]
    assert statuses(day(pop, date(2022, 8, 31)), "followed") == ["already_assigned"]


def test_validity_censoring_excludes_only_events_already_started() -> None:
    rows = [*BASE, (utc(30, 16), "opener", utc(30, 13, 50, 12))]  # in-play stamp: 13:50:12
    pop = apply_validity_censoring(
        day_anchor_population(listings(rows), calendar=CAL), listings(rows)
    )
    d30 = day(pop, date(2022, 8, 30))
    assert statuses(d30, "opener") == ["started_before_decision"]
    assert statuses(d30, "followed") == ["in_population"]


@pytest.mark.parametrize(
    ("offset", "admissible"),
    [
        (timedelta(0), True),
        (timedelta(minutes=-9, seconds=-59), True),
        (timedelta(minutes=-10), False),
        (timedelta(minutes=-10, seconds=-1), False),
        (timedelta(seconds=1), False),
    ],
)
def test_as_of_snapshot_window_boundaries(offset: timedelta, admissible: bool) -> None:
    decision = utc(30, 14)
    assert (as_of_snapshot([decision + offset], decision) is not None) is admissible


def test_comparison_candidates_still_run() -> None:
    rows = [
        (utc(30, 3, 55), "opener", utc(30, 15)),  # 23:55 EDT 08-29, before K_d
        (utc(30, 13, 55), "opener", utc(30, 15)),
        (utc(30, 15, 55), "m", utc(30, 16, 46)),
    ]
    a1 = opening_anchor_population(listings(rows), calendar=CAL, days=[date(2022, 8, 30)])
    assert (a1["decision_utc"] == utc(30, 14)).all()
    b = match_pit_decisions(listings(rows), sport_key=USO)
    assert b.filter(pl.col("provider_event_id") == "m")["decision_utc"].to_list() == [
        utc(30, 15, 55)
    ]


@settings(max_examples=100, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=47),
            st.integers(min_value=0, max_value=5),
            st.integers(min_value=-120, max_value=24 * 60),
        ),
        min_size=1,
        max_size=40,
    )
)
def test_day_anchor_point_in_time_invariants(spec: list[tuple[int, int, int]]) -> None:
    rows = []
    for slot, eid, offset in spec:
        snap = utc(30, 4) + timedelta(minutes=30 * slot)
        rows.append((snap, f"e{eid}", snap + timedelta(minutes=offset)))
    frame = listings(rows).unique(subset=["snapshot_utc", "provider_event_id"], keep="first")
    inpop = day_anchor_population(frame, calendar=CAL).filter(pl.col("status") == "in_population")
    for r in inpop.iter_rows(named=True):
        assert (
            r["decision_utc"] - timedelta(minutes=10) < r["as_of_snapshot_utc"] <= r["decision_utc"]
        )
        assert r["as_of_start_utc"] - r["decision_utc"] >= timedelta(minutes=60)
        listed = frame.filter(
            (pl.col("snapshot_utc") == r["as_of_snapshot_utc"])
            & (pl.col("provider_event_id") == r["provider_event_id"])
        )
        assert listed["commence_utc"].to_list() == [r["as_of_start_utc"]]
    assert inpop["provider_event_id"].n_unique() == inpop.height


# ------------------------------------------------------------------------------------ identity


def prod_links(rows: list[tuple[str, str, str | None]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "event_id": [f"ev_{r[0]}" for r in rows],
            "status": [r[1] for r in rows],
            "reason": ["" for _ in rows],
            "match_key": [r[2] for r in rows],
            "participant_1_side": ["A" for _ in rows],
        }
    )


def prod_events(pids: list[str]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "event_id": [f"ev_{p}" for p in pids],
            "provider_event_id": pids,
            "competition_key": [USO for _ in pids],
        }
    )


def test_identity_adapter_maps_the_production_schema() -> None:
    ident = identity_links(
        prod_links([("a", "linked", "M1"), ("b", "ambiguous", None)]), prod_events(["a", "b"])
    )
    assert ident.columns == [
        "provider_event_id", "competition_key", "event_id", "match_key", "link_status",
        "participant_1_side",
    ]  # fmt: skip
    assert ident.sort("provider_event_id")["link_status"].to_list() == ["linked", "ambiguous"]


@pytest.mark.parametrize(
    ("links", "events", "match"),
    [
        (prod_links([("a", "linked", "M1")]).drop("status"), prod_events(["a"]), "lacks columns"),
        (pl.concat([prod_links([("a", "linked", "M1")])] * 2), prod_events(["a"]), "not unique"),
        (prod_links([("a", "linked", None)]), prod_events(["a"]), "no match_key"),
        (prod_links([("z", "linked", "M1")]), prod_events(["a"]), "no odds event"),
    ],
)
def test_identity_adapter_fails_loudly(
    links: pl.DataFrame, events: pl.DataFrame, match: str
) -> None:
    with pytest.raises(V2IntegrationError, match=match):
        identity_links(links, events)


def test_concurrent_claimants_are_excluded_and_never_rescued() -> None:
    rows = [
        (utc(30, 13, 55), "a1", utc(30, 15)),
        (utc(30, 13, 55), "a2", utc(31, 15)),  # twin listed concurrently for *another* day
        (utc(31, 13, 55), "a2", utc(31, 15)),  # next day only one twin: still no rescue
        (utc(30, 13, 55), "b1", utc(30, 19)),  # store-wide duplicate, twin never co-listed
        (utc(31, 13, 55), "b2", utc(31, 19)),
        (utc(30, 13, 55), "c", utc(30, 21)),
        (utc(30, 13, 55), "x", utc(30, 22)),
        (utc(30, 13, 55), "amb", utc(30, 23)),
    ]
    ident = identity_links(
        prod_links(
            [
                ("a1", "duplicate_provider_event", "M1"),
                ("a2", "duplicate_provider_event", "M1"),
                ("b1", "duplicate_provider_event", "M2"),
                ("b2", "duplicate_provider_event", "M2"),
                ("c", "linked", "M3"),
                ("x", "unmatched", None),
                ("amb", "ambiguous", None),
            ]
        ),
        prod_events(["a1", "a2", "b1", "b2", "c", "x", "amb"]),
    )
    frame = listings(rows)
    out = resolve_identity(day_anchor_population(frame, calendar=CAL), ident, frame)
    got = {
        (r["provider_event_id"], r["local_day"].day): r["identity"]
        for r in out.iter_rows(named=True)
    }
    assert got[("a1", 30)] == "identity_conflict"
    assert got[("a2", 31)] == "identity_conflict"
    assert got[("b1", 30)] == "unique" and got[("b2", 31)] == "already_assigned"
    assert got[("c", 30)] == "unique"
    assert got[("x", 30)] == got[("amb", 30)] == "not_linked"


# ------------------------------------------------------------------------- manifest and runner


def store_at(tmp_path: Path) -> CaptureStore:
    return CaptureStore(tmp_path, "the_odds_api", LicenceClass.COMMERCIAL_LICENSED, "historical")


def client_for(
    responses: list[HttpResponse], budget: int
) -> tuple[TheOddsApiClient, ScriptedTransport]:
    transport = ScriptedTransport(responses)
    config = OddsApiConfig(max_credits_per_run=budget, max_retries=0)
    return TheOddsApiClient(config, api_key="k", transport=transport), transport


def test_manifest_is_calendar_bound_strict_and_sentinel_only() -> None:
    m = day_anchor_manifest(name="syn", calendar=CAL, days=[date(2022, 8, 30), date(2022, 8, 31)])
    assert m.strict_snapshot_window and m.planned_credits == 20 and m.max_gap_minutes == 10.0
    assert m.requests[0].request_date_utc == "2022-08-30T14:00:00Z"
    assert manifest_days(m, CAL) == (date(2022, 8, 30), date(2022, 8, 31))
    assert roundtrips(m)
    with pytest.raises(V2IntegrationError, match="outside the frozen calendar"):
        day_anchor_manifest(name="x", calendar=CAL, days=[date(2022, 9, 5)])
    tampered = json.loads(m.to_json())
    tampered["requests"][0]["request_date_utc"] = "2022-08-30T13:00:00Z"
    with pytest.raises(V2IntegrationError, match="does not match the frozen calendar"):
        manifest_days(StageManifest.from_json(json.dumps(tampered)), CAL)
    with pytest.raises(StagePlanError, match="v2 audit"):
        stage_audit(
            m, events=pl.DataFrame(), quotes=pl.DataFrame(), quarantine=pl.DataFrame(), ledger=[]
        )


def roundtrips(m: object) -> bool:
    assert isinstance(m, StageManifest)
    return StageManifest.from_json(m.to_json()) == m


def test_legacy_sentinel_manifest_and_sentinel_ids_are_refused(tmp_path: Path) -> None:
    m = day_anchor_manifest(name="syn", calendar=CAL, days=[date(2022, 8, 30)])
    raw = json.loads(m.to_json())
    raw.pop("strict_snapshot_window")  # as written by the earlier draft builder
    legacy = StageManifest.from_json(json.dumps(raw))
    with pytest.raises(StageRunStoppedError, match="legacy"):
        run_stage(legacy, store=store_at(tmp_path), ledger_path=tmp_path / "l.jsonl")
    with pytest.raises(StagePlanError, match="sentinel"):
        plan_stage(
            name="x", sport_key=USO, discovery_capture_id="c", discovery_snapshot=utc(30, 11, 55),
            events=[NormalizedEvent("__day_anchor__x", USO, None, "a", "b", utc(30, 15))],
        )  # fmt: skip


@pytest.mark.parametrize(
    ("returned", "stops", "reason"),
    [
        (utc(30, 14), False, None),
        (utc(30, 13, 50, 1), False, None),  # T - 9:59
        (utc(30, 13, 50), True, "older than planned"),  # T - 10:00 exactly: outside (strict)
        (utc(30, 13, 49, 59), True, "older than planned"),  # T - 10:01
        (utc(30, 14, 0, 1), True, "post-request"),
    ],
)
def test_runner_enforces_the_strict_snapshot_window(
    tmp_path: Path, returned: datetime, stops: bool, reason: str | None
) -> None:
    m = day_anchor_manifest(name="syn", calendar=CAL, days=[date(2022, 8, 30)])
    client, transport = client_for([ok(historical_body(returned, []), last=10, remaining=990)], 10)
    if stops:
        with pytest.raises(StageRunStoppedError, match=reason):
            run_stage(
                m,
                store=store_at(tmp_path),
                ledger_path=tmp_path / "l.jsonl",
                client=client,
                execute=True,
            )
    else:
        run_stage(
            m,
            store=store_at(tmp_path),
            ledger_path=tmp_path / "l.jsonl",
            client=client,
            execute=True,
        )
    assert len(transport.urls) == 1  # never a second request for the same day


def test_sentinels_never_count_as_events(tmp_path: Path) -> None:
    m = day_anchor_manifest(name="syn", calendar=CAL, days=[date(2022, 8, 30)])
    body = historical_body(
        utc(30, 13, 55), [event("real", "A", "B", utc(30, 15), [("bk", utc(30, 13, 50), 1.8, 2.1)])]
    )
    client, _ = client_for([ok(body, last=10, remaining=990)], 10)
    report = run_stage(
        m, store=store_at(tmp_path), ledger_path=tmp_path / "l.jsonl", client=client, execute=True
    )
    entry = read_ledger(tmp_path / "l.jsonl")[0]
    assert (entry.events_planned, entry.events_present) == (0, 0) and report[0]["events"] == 0
    dry = run_stage(
        day_anchor_manifest(name="syn2", calendar=CAL, days=[date(2022, 8, 31)]),
        store=store_at(tmp_path / "other"),
        ledger_path=tmp_path / "l2.jsonl",
    )
    assert dry[0]["events"] == 0


def test_restart_after_a_stop_skips_paid_days_and_never_retimes(tmp_path: Path) -> None:
    cal = EditionCalendar(**{**CAL.__dict__, "rest_days": ()})
    days = [date(2022, 8, 30), date(2022, 8, 31), date(2022, 9, 1)]
    m = day_anchor_manifest(name="syn", calendar=cal, days=days)
    store, ledger = store_at(tmp_path), tmp_path / "l.jsonl"
    first, t1 = client_for(
        [
            ok(historical_body(utc(30, 13, 55), []), last=10, remaining=990),
            ok(historical_body(utc(31, 13, 40), []), last=10, remaining=980),  # 20 min old: stop
        ],
        30,
    )
    with pytest.raises(StageRunStoppedError):
        run_stage(m, store=store, ledger_path=ledger, client=first, execute=True)
    second, t2 = client_for(
        [ok(historical_body(utc(1, 13, 55, month=9), []), last=10, remaining=970)], 30
    )
    report = run_stage(m, store=store, ledger_path=ledger, client=second, execute=True)
    assert [r["action"] for r in report] == [
        "skip_already_captured",
        "skip_already_captured",
        "requested",
    ]
    assert len(t1.urls) == 2 and len(t2.urls) == 1
    assert [e.cumulative_credits for e in read_ledger(ledger)] == [10, 20, 30]
    pop = day_anchor_population(schedule_listings(store), calendar=cal, days=days)
    assert day(pop, date(2022, 8, 31))["status"].to_list() == ["snapshot_missing"]  # not rescued


def test_lock_is_write_once_and_blocks_further_requests(tmp_path: Path) -> None:
    m = day_anchor_manifest(name="syn", calendar=CAL, days=[date(2022, 8, 30)])
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    (stage_dir / "manifest.json").write_text(m.to_json(), encoding="utf-8")
    store = store_at(tmp_path)
    client, _ = client_for([ok(historical_body(utc(30, 13, 55), []), last=10, remaining=990)], 10)
    run_stage(m, store=store, ledger_path=stage_dir / "ledger.jsonl", client=client, execute=True)
    lock = write_lock(stage_dir, store)
    assert lock["credits"] == 10 and len(lock["captures"]) == 1 and (stage_dir / LOCK_FILE).exists()
    with pytest.raises(V2IntegrationError, match="already exists"):
        write_lock(stage_dir, store)
    with pytest.raises(StageRunStoppedError, match="locked"):
        run_stage(m, store=store, ledger_path=stage_dir / "ledger.jsonl")


# ------------------------------------------------------------------------------- v2 audit e2e


def test_v2_audit_end_to_end_on_synthetic_captures(tmp_path: Path) -> None:
    cal = EditionCalendar(**{**CAL.__dict__, "rest_days": ()})
    days = [date(2022, 8, 30), date(2022, 8, 31)]
    m = day_anchor_manifest(name="syn", calendar=cal, days=days)
    books = [("bk1", utc(30, 13, 50), 1.8, 2.1), ("bk2", utc(30, 13, 50), 1.75, 2.15)]
    clone = [("bk1", utc(30, 13, 50), 1.8, 2.1), ("bk1b", utc(30, 13, 50), 1.8, 2.1)]
    day1 = historical_body(
        utc(30, 13, 55),
        [
            event("p1", "A1", "B1", utc(30, 15), books, sport_key=USO),
            event("p2", "A2", "B2", utc(30, 19), clone, sport_key=USO),  # one distinct source
            event("p3", "A3", "B3", utc(30, 14, 30), books, sport_key=USO),  # short lead
            event("p4", "A4", "B4", utc(31, 16), books, sport_key=USO),  # tomorrow
        ],
    )
    late = [("bk1", utc(31, 13, 50), 1.9, 2.0), ("bk2", utc(31, 13, 50), 1.85, 2.05)]
    day2 = historical_body(
        utc(31, 13, 55), [event("p4", "A4", "B4", utc(31, 16), late, sport_key=USO)]
    )
    store = store_at(tmp_path)
    client, _ = client_for([ok(day1, last=10, remaining=990), ok(day2, last=10, remaining=980)], 20)
    run_stage(m, store=store, ledger_path=tmp_path / "l.jsonl", client=client, execute=True)
    built = build_odds_tables_with_quarantine(store, TheOddsApiNormalizer())  # type: ignore[arg-type]
    links = pl.DataFrame(
        {
            "event_id": built.events["event_id"],
            "status": "linked",
            "reason": "",
            "match_key": [f"M_{p}" for p in built.events["provider_event_id"]],
            "participant_1_side": "A",
        }
    )
    canonical = pl.DataFrame(
        {
            "match_key": ["M_p1", "M_p2", "M_p3", "M_p4", "M_never_listed"],
            "tournament_id": "t-syn",
            "draw_category": "tour_main",
            "match_day_lo": [date(2022, 8, 30)] * 4 + [date(2022, 8, 31)],
            "match_day_hi": [date(2022, 8, 31)] * 5,
            "prediction_cutoff_utc": [utc(29, 10)] * 5,
        }
    ).with_columns(pl.col("prediction_cutoff_utc").cast(pl.Datetime("us", "UTC")))
    report = v2_audit(
        calendar=cal,
        manifests=[(m, read_ledger(tmp_path / "l.jsonl"), False)],
        listings=schedule_listings(store),
        events=built.events,
        quotes=built.quotes,
        identity=identity_links(links, built.events),
        canonical_matches=canonical,
    )
    d = report["denominators"]
    assert d["canonical_main_draw_certainly_in_days"] == 5
    assert d["listed_in_population_events"] == 3  # p1, p2 (day 1), p4 (day 2)
    assert d["unique_matches"] == 3
    assert d["fresh30_ge1_events"] == 3
    assert d["fresh30_ge2_distinct_matches"] == 2  # the clone pair is one source
    assert d["devig_usable_events"] == 3
    assert report["population_status"]["short_lead"] == 1
    assert report["credits_spent"] == 20 and report["all_stages_locked"] is False
    assert [x["status"] for x in report["days"]] == ["observed", "observed"]
