"""Exchange lay prices (``h2h_lay``) never enter match-winner selection or probabilities."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from quantedge.core.lineage import LicenceClass
from quantedge.odds.selector import SelectorConfig, select_quotes
from quantedge.odds.store import CaptureStore
from quantedge.odds.tables import build_odds_tables
from quantedge.odds.theoddsapi import OddsApiConfig, TheOddsApiClient, TheOddsApiNormalizer
from tests.fixtures.odds_synthetic import ScriptedTransport, event, ok, outcome_market

SNAP = datetime(2022, 6, 28, 11, 55, tzinfo=UTC)
START = SNAP + timedelta(hours=1, minutes=30)


def test_lay_market_is_not_a_match_winner_price(tmp_path: Path) -> None:
    last = SNAP - timedelta(minutes=4)
    ev = event("ev", "Lay Alpha", "Lay Beta", START, [("exch", last, 1.8, 2.1)])
    # The exchange also publishes lay prices for the same runners, with the same selection names.
    ev["bookmakers"][0]["markets"].append(
        outcome_market("h2h_lay", last, [("Lay Alpha", 1.85), ("Lay Beta", 2.2)])
    )
    payload = {
        "timestamp": "2022-06-28T11:55:00Z",
        "previous_timestamp": None,
        "next_timestamp": None,
        "data": [ev],
    }
    client = TheOddsApiClient(
        OddsApiConfig(max_credits_per_run=10),
        api_key="k",
        transport=ScriptedTransport([ok(json.dumps(payload).encode(), last=10)]),
        clock=lambda: SNAP,
    )
    store = CaptureStore(tmp_path, "the_odds_api", LicenceClass.COMMERCIAL_LICENSED, "historical")
    store.append(
        client.historical_odds("tennis_atp_wimbledon", SNAP + timedelta(minutes=5), approved=True)
    )
    events, quotes = build_odds_tables(store, TheOddsApiNormalizer())  # type: ignore[arg-type]
    lay = quotes.filter(pl.col("provider_market_key") == "h2h_lay")
    assert lay.height == 2 and set(lay["market"]) == {"other"}
    assert lay["quality_flags"].str.contains("unknown_market").all()
    decisions = pl.DataFrame(
        {"event_id": events["event_id"], "decision_time_utc": [SNAP + timedelta(minutes=10)]}
    ).with_columns(pl.col("decision_time_utc").cast(pl.Datetime("us", "UTC")))
    out = select_quotes(quotes, decisions, SelectorConfig(max_staleness_minutes=30))
    row = out.row(0, named=True)
    # Exactly one complete pair, built from the back (h2h) prices only.
    assert out.height == 1 and row["status"] == "available"
    assert sorted([row["odds_1"], row["odds_2"]]) == [1.8, 2.1]
    assert {row["quote_id_1"], row["quote_id_2"]}.isdisjoint(set(lay["quote_id"]))
