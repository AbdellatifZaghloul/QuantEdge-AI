"""Phase 3B timing integrity: clock-skewed, missing-source, late-snapshot and in-play quotes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
from hypothesis import given, settings
from hypothesis import strategies as st

from quantedge.core.lineage import LicenceClass
from quantedge.odds.selector import SelectorConfig, decisions_from_schedule, select_quotes
from quantedge.odds.store import CaptureStore
from quantedge.odds.tables import build_odds_tables
from quantedge.odds.theoddsapi import OddsApiConfig, TheOddsApiClient, TheOddsApiNormalizer
from tests.fixtures.odds_synthetic import ScriptedTransport, event, historical_body, ok

UTC_DT = pl.Datetime("us", "UTC")
START = datetime(2022, 6, 28, 13, 0, tzinfo=UTC)
T = START - timedelta(minutes=60)


def quotes(rows: list[dict[str, object]]) -> pl.DataFrame:
    base = {
        "market": "match_winner",
        "timing_class": "timestamped",
        "commence_time_utc": START,
        "quality_flags": "",
        "event_id": "e",
    }
    data = []
    for i, r in enumerate(rows):
        row = base | r
        row.setdefault("quote_id", f"q{i}")
        data.append(row)
    return pl.DataFrame(data).with_columns(
        pl.col("observed_at_utc", "commence_time_utc", "market_last_update_utc").cast(UTC_DT)
    )


def pair(
    book: str, observed: datetime, source: datetime | None, **kw: object
) -> list[dict[str, object]]:
    common = {"bookmaker_key": book, "observed_at_utc": observed, "market_last_update_utc": source}
    return [
        common | {"selection_name": "P One", "decimal_odds": 1.6} | kw,
        common | {"selection_name": "P Two", "decimal_odds": 2.4} | kw,
    ]


def decisions(t: datetime = T) -> pl.DataFrame:
    return pl.DataFrame({"event_id": ["e"], "decision_time_utc": [t]}).with_columns(
        pl.col("decision_time_utc").cast(UTC_DT)
    )


def statuses(q: pl.DataFrame, config: SelectorConfig | None = None) -> dict[object, object]:
    out = select_quotes(q, decisions(), config)
    return dict(zip(out["bookmaker_key"], out["status"], strict=True))


def test_source_later_than_observation_is_quarantined() -> None:
    q = quotes(pair("bad", T - timedelta(minutes=5), T - timedelta(minutes=1)))
    assert statuses(q) == {None: "quarantined_timestamp"}


def test_source_later_than_decision_is_quarantined() -> None:
    # Observation before the decision but source stamp after it: clock-inconsistent -> quarantine.
    q = quotes(pair("bad", T - timedelta(minutes=5), T + timedelta(minutes=2)))
    assert statuses(q) == {None: "quarantined_timestamp"}


def test_blocking_flags_are_quarantined() -> None:
    for flag in ("snapshot_after_request", "observed_after_commence", "source_after_observation"):
        q = quotes(
            pair("b", T - timedelta(minutes=5), T - timedelta(minutes=6), quality_flags=flag)
        )
        assert statuses(q) == {None: "quarantined_timestamp"}, flag
    benign = quotes(
        pair(
            "b",
            T - timedelta(minutes=5),
            T - timedelta(minutes=6),
            quality_flags="odds_out_of_range",
        )
    )
    assert statuses(benign) == {"b": "available"}


def test_missing_source_time_rejected_by_default_and_visible() -> None:
    q = quotes(pair("b", T - timedelta(minutes=5), None))
    assert statuses(q) == {None: "source_time_missing"}
    lenient = statuses(q, SelectorConfig(missing_source_time="use_observed"))
    assert lenient == {"b": "available"}


def test_valid_quote_beside_invalid_ones_is_still_selected() -> None:
    q = quotes(
        pair("good", T - timedelta(minutes=10), T - timedelta(minutes=11))
        + pair("bad", T - timedelta(minutes=5), T - timedelta(minutes=1))
        + pair("inplay", START + timedelta(minutes=1), START)
    )
    assert statuses(q) == {"good": "available"}


@settings(max_examples=300, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.sampled_from(["b1", "b2"]),
            st.integers(-240, 90),
            st.one_of(st.none(), st.integers(-30, 30)),
        ),
        min_size=1,
        max_size=20,
    )
)
def test_selected_rows_always_satisfy_timing_invariants(
    obs: list[tuple[str, int, int | None]],
) -> None:
    rows: list[dict[str, object]] = []
    for book, minutes, lag in obs:
        observed = T + timedelta(minutes=minutes)
        source = None if lag is None else observed + timedelta(minutes=lag)
        rows += pair(book, observed, source)
    q = quotes(rows).unique(
        subset=["bookmaker_key", "observed_at_utc", "selection_name"], keep="first"
    )
    out = select_quotes(q, decisions(), SelectorConfig(max_staleness_minutes=10_000))
    chosen = out.filter(pl.col("status").is_in(["available", "stale"]))
    assert (chosen["source_time"].is_not_null()).all()
    assert (chosen["source_time"] <= chosen["observed_at_utc"]).all()
    assert (chosen["observed_at_utc"] <= T).all() and (chosen["observed_at_utc"] < START).all()


# ----------------------------------------------------------------------------- normalisation


def _store_with(tmp_path: Path, responses: list[tuple[datetime, bytes]]) -> CaptureStore:
    transport = ScriptedTransport([ok(body, last=10) for _, body in responses])
    client = TheOddsApiClient(
        OddsApiConfig(max_credits_per_run=100),
        api_key="k",
        transport=transport,
        clock=lambda: START,
    )
    store = CaptureStore(tmp_path, "the_odds_api", LicenceClass.COMMERCIAL_LICENSED, "historical")
    for requested, _ in responses:
        store.append(client.historical_odds("tennis_atp_wimbledon", requested, approved=True))
    return store


def test_snapshot_later_than_requested_is_flagged_and_quarantined(tmp_path: Path) -> None:
    requested = T - timedelta(minutes=20)
    late_snapshot = requested + timedelta(minutes=5)  # provider must return <= requested
    ev = event(
        "ev", "P One", "P Two", START, [("b", late_snapshot - timedelta(minutes=1), 1.6, 2.4)]
    )
    store = _store_with(tmp_path, [(requested, historical_body(late_snapshot, [ev]))])
    events, quotes_ = build_odds_tables(store, TheOddsApiNormalizer())  # type: ignore[arg-type]
    assert quotes_["quality_flags"].str.contains("snapshot_after_request").all()
    out = select_quotes(quotes_, decisions_from_schedule(events, 60))
    assert out["status"].to_list() == ["quarantined_timestamp"]


def test_rescheduled_event_uses_earliest_announced_start(tmp_path: Path) -> None:
    later_start = START + timedelta(hours=3)
    s1, s2 = T - timedelta(hours=5), T + timedelta(hours=2)
    store = _store_with(
        tmp_path,
        [
            (
                s1,
                historical_body(s1, [event("ev", "P One", "P Two", START, [("b", s1, 1.6, 2.4)])]),
            ),
            (
                s2,
                historical_body(
                    s2, [event("ev", "P One", "P Two", later_start, [("b", s2, 1.5, 2.6)])]
                ),
            ),
        ],
    )
    events, quotes_ = build_odds_tables(store, TheOddsApiNormalizer())  # type: ignore[arg-type]
    row = events.row(0, named=True)
    assert row["commence_time_utc"] == later_start and row["commence_time_earliest_utc"] == START
    assert row["commence_time_versions"] == 2
    assert decisions_from_schedule(events, 60)["decision_time_utc"][0] == START - timedelta(
        minutes=60
    )
    assert quotes_["quality_flags"].str.contains("commence_time_changed").all()
