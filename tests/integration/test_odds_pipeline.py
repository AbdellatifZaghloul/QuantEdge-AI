"""End to end on synthetic data: capture -> store -> tables -> link -> point-in-time select ->
de-vig -> comparison with a model on the identical population (holdout refused)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from quantedge.core.chronology import earliest_instant
from quantedge.core.lineage import LicenceClass
from quantedge.odds.baseline import (
    HoldoutLeakError,
    attach_participant_order,
    compare_on_identical_population,
    consensus,
    market_probabilities,
)
from quantedge.odds.identity import PlayerResolver, link_events
from quantedge.odds.selector import SelectorConfig, select_quotes
from quantedge.odds.store import CaptureStore
from quantedge.odds.tables import build_odds_tables
from quantedge.odds.theoddsapi import OddsApiConfig, TheOddsApiClient, TheOddsApiNormalizer
from tests.fixtures.odds_synthetic import ScriptedTransport, event, historical_body, ok
from tests.leakage.scenario import frame, mk

WIM = "sackmann:2024-540"
NAMES = {f"sackmann:{900 + i}": (f"First{i}", f"Last{i}") for i in range(8)}


def canonical() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    players = pl.DataFrame(
        {
            "player_id": list(NAMES),
            "name_first": [v[0] for v in NAMES.values()],
            "name_last": [v[1] for v in NAMES.values()],
        }
    )
    ids = list(NAMES)
    rows = [
        mk(WIM, "R128", ids[2 * i], ids[2 * i + 1], date(2024, 7, 1), date(2024, 7, 4))
        for i in range(4)
    ]
    matches = frame(rows).with_columns(pl.col("match_day_lo").alias("tournament_start_date"))
    tournaments = pl.DataFrame({"tournament_id": [WIM], "name": ["Wimbledon"], "level": ["G"]})
    return players, matches, tournaments


def full(pid: str) -> str:
    return " ".join(NAMES[pid])


def test_pipeline(tmp_path: Path) -> None:
    players, matches, tournaments = canonical()
    commence = datetime(2024, 7, 2, 13, 0, tzinfo=UTC)
    snapshots = [commence - timedelta(hours=h) for h in (30, 20, 2)]
    responses = []
    for k, snap in enumerate(snapshots):
        evs = []
        for i, m in enumerate(matches.iter_rows(named=True)):
            winner_first = m["winner_id"] == m["player_a_id"]
            fav, dog = (1.5 + 0.05 * k, 2.6 - 0.05 * k)
            o1, o2 = (fav, dog) if winner_first else (dog, fav)
            evs.append(
                event(
                    f"ev{i}",
                    full(m["player_a_id"]),
                    full(m["player_b_id"]),
                    commence,
                    [
                        ("bookone", snap - timedelta(minutes=1), o1, o2),
                        ("booktwo", snap - timedelta(minutes=3), o1 + 0.02, o2 - 0.04),
                    ],
                )
            )
        responses.append(ok(historical_body(snap, evs), last=10))
    client = TheOddsApiClient(
        OddsApiConfig(max_credits_per_run=30),
        api_key="k",
        transport=ScriptedTransport(responses),
        clock=lambda: snapshots[-1],
    )
    store = CaptureStore(tmp_path, "the_odds_api", LicenceClass.COMMERCIAL_LICENSED, "historical")
    for snap in snapshots:
        store.append(client.historical_odds("tennis_atp_wimbledon", snap, approved=True))
    assert client.credits_spent == 30

    events, quotes = build_odds_tables(store, TheOddsApiNormalizer())  # type: ignore[arg-type]
    appearances = pl.DataFrame(
        {"player_id": list(NAMES), "tournament_start_date": [date(2024, 6, 1)] * 8}
    )
    links = link_events(
        events,
        matches,
        tournaments,
        PlayerResolver(players, appearances, "the_odds_api"),
        provider="the_odds_api",
    )
    assert links["status"].to_list() == ["linked"] * 4

    config = SelectorConfig(max_staleness_minutes=24 * 60)
    # The canonical day-bound cutoff (earliest instant of the first possible day) precedes every
    # snapshot here, so the strict selector correctly finds nothing.
    day_bound = links.join(
        matches.select("match_key", "prediction_cutoff_utc"), on="match_key"
    ).select("event_id", pl.col("prediction_cutoff_utc").alias("decision_time_utc"))
    assert day_bound["decision_time_utc"][0] == earliest_instant(date(2024, 7, 1))
    assert set(select_quotes(quotes, day_bound, config)["status"]) == {"no_quote_before_cutoff"}

    # Market decision time from the provider's scheduled start minus a 60-minute lead.
    decisions = events.select(
        "event_id", (pl.col("commence_time_utc") - timedelta(minutes=60)).alias("decision_time_utc")
    )
    selected = attach_participant_order(select_quotes(quotes, decisions, config), events)
    usable = selected.filter(pl.col("status") == "available")
    # The 2-hour-before snapshot is the latest one at or before the decision time.
    assert set(usable["observed_at_utc"]) == {snapshots[2]}
    probs = market_probabilities(selected, links)
    assert probs["usable"].all() and probs.height == 8
    market = consensus(probs, "p_shin_a")
    assert (market["n_bookmakers"] == 2).all()

    evaluation = market.join(
        matches.select("match_key", "a_won", "tournament_start_date"), on="match_key"
    ).with_columns(
        pl.col("a_won").cast(pl.Int8).alias("y"),
        pl.lit(0.5).alias("p_model"),
        pl.lit(None, dtype=pl.Float64).alias("p_missing"),
    )
    result = compare_on_identical_population(
        evaluation, ["p_model", "p_shin_a"], holdout_start=date(2025, 1, 1), replicates=50
    )
    assert (
        result["n_common"] == 4
        and result["metrics"]["p_shin_a"]["log_loss"] < result["metrics"]["p_model"]["log_loss"]
    )
    dropped = compare_on_identical_population(
        evaluation, ["p_model", "p_missing"], holdout_start=date(2025, 1, 1), replicates=10
    )
    assert dropped["n_common"] == 0 and dropped["dropped_missing_any"] == 4

    holdout_like = evaluation.with_columns(pl.lit(date(2025, 6, 30)).alias("tournament_start_date"))
    with pytest.raises(HoldoutLeakError):
        compare_on_identical_population(
            holdout_like, ["p_model", "p_shin_a"], holdout_start=date(2025, 1, 1)
        )


def test_probability_side_mapping_is_orientation_correct() -> None:
    links = pl.DataFrame(
        {
            "event_id": ["e1", "e2"],
            "status": ["linked", "linked"],
            "match_key": ["m1", "m2"],
            "participant_1_side": ["A", "B"],
        }
    )
    selected = pl.DataFrame(
        {
            "event_id": ["e1", "e2"],
            "decision_time_utc": [datetime(2024, 1, 1, tzinfo=UTC)] * 2,
            "bookmaker_key": ["b", "b"],
            "observed_at_utc": [datetime(2024, 1, 1, tzinfo=UTC)] * 2,
            "status": ["available"] * 2,
            "odds_1": [1.25, 1.25],
            "odds_2": [5.0, 5.0],
            "selection_1_is_participant_1": [True, False],
        }
    )
    p = market_probabilities(selected, links, methods=("proportional",)).sort("event_id")
    # e1: selection_1 = participant_1 = A; e2: selection_1 = participant_2 = A (both favourites)
    assert p["p_proportional_a"].to_list() == pytest.approx([0.8, 0.8])
