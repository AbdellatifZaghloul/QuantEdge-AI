"""Quarantine of structurally invalid provider prices (first-real-capture regression, synthetic)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from quantedge.core.lineage import LicenceClass
from quantedge.odds.schemas import validate_odds_table
from quantedge.odds.selector import SelectorConfig, decisions_from_schedule, select_quotes
from quantedge.odds.store import CaptureStore
from quantedge.odds.tables import OddsBuild, build_odds_tables, build_odds_tables_with_quarantine
from quantedge.odds.theoddsapi import (
    OddsApiConfig,
    TheOddsApiClient,
    TheOddsApiNormalizer,
    parse_decimal_price,
)
from tests.fixtures.odds_synthetic import ScriptedTransport, event, ok

SNAP = datetime(2022, 1, 18, 11, 55, tzinfo=UTC)
REQUESTED = datetime(2022, 1, 18, 12, 0, tzinfo=UTC)
START = SNAP + timedelta(hours=14)


def body(events: list[dict[str, Any]]) -> bytes:
    payload = {
        "timestamp": "2022-01-18T11:55:00Z",
        "previous_timestamp": None,
        "next_timestamp": None,
        "data": events,
    }
    return json.dumps(payload, allow_nan=True).encode()


def two_events() -> list[dict[str, Any]]:
    last = SNAP - timedelta(minutes=5)
    return [
        event(
            "ev1",
            "Alpha One",
            "Beta One",
            START,
            [("bookx", last, 1.0, 23.0), ("booky", last, 1.0, 22.0), ("bookz", last, 1.01, 9.0)],
        ),
        event(
            "ev2",
            "Gamma Two",
            "Delta Two",
            START,
            [("bookx", last, 1.6, 2.3), ("booky", last, 1.62, 2.25)],
        ),
    ]


def build(tmp_path: Path, payload: bytes) -> tuple[OddsBuild, CaptureStore]:
    transport = ScriptedTransport([ok(payload, last=10)])
    client = TheOddsApiClient(
        OddsApiConfig(max_credits_per_run=10),
        api_key="k",
        transport=transport,
        clock=lambda: SNAP + timedelta(days=1000),
    )
    store = CaptureStore(tmp_path, "the_odds_api", LicenceClass.COMMERCIAL_LICENSED, "historical")
    store.append(client.historical_odds("tennis_atp_aus_open_singles", REQUESTED, approved=True))
    return build_odds_tables_with_quarantine(store, TheOddsApiNormalizer()), store  # type: ignore[arg-type]


def test_two_prices_equal_to_one_are_quarantined_not_repaired(tmp_path: Path) -> None:
    result, _ = build(tmp_path, body(two_events()))
    q = result.quarantine
    assert q.height == 2 and set(q["reason_code"]) == {"price_not_above_one"}
    assert set(q["bookmaker_key"]) == {"bookx", "booky"} and set(q["decimal_odds"]) == {1.0}
    assert set(q["price_raw"]) == {"1.0"} and q["snapshot_sha256"].n_unique() == 1
    assert (result.quotes["decimal_odds"] > 1.0).all()  # canonical constraint untouched
    assert result.counts == {
        "raw_quotes": 10,
        "distinct_quotes": 10,
        "valid_quotes": 8,
        "quarantined_quotes": 2,
        "quarantined_by_reason": {"price_not_above_one": 2},
        "events": 2,
        "incomplete_sibling_quotes": 2,
    }
    # Short but valid prices are kept, with the documented quality flag (not quarantined).
    short = result.quotes.filter(pl.col("decimal_odds") == 1.01)
    assert short.height == 1 and "odds_out_of_range" in short["quality_flags"][0]
    assert set(result.quotes["bookmaker_key"]) == {"bookx", "booky", "bookz"}


def test_incomplete_market_after_quarantine_yields_no_probability(tmp_path: Path) -> None:
    result, _ = build(tmp_path, body(two_events()))
    siblings = result.quotes.filter(
        pl.col("quality_flags").str.contains("market_incomplete_after_quarantine")
    )
    assert siblings.height == 2 and set(siblings["decimal_odds"]) == {23.0, 22.0}
    out = select_quotes(
        result.quotes,
        decisions_from_schedule(result.events, 60),
        SelectorConfig(max_staleness_minutes=24 * 60),
    )
    ev1 = result.events.filter(pl.col("provider_event_id") == "ev1")["event_id"][0]
    chosen = out.filter(pl.col("event_id") == ev1)
    # Only bookz offers a complete two-way market on ev1; bookx/booky halves are never paired.
    assert chosen["bookmaker_key"].to_list() == ["bookz"]
    assert chosen["status"].to_list() == ["available"]


def test_all_valid_output_is_unchanged_and_nothing_quarantined(tmp_path: Path) -> None:
    last = SNAP - timedelta(minutes=5)
    payload = body([event("ev2", "Gamma Two", "Delta Two", START, [("bookx", last, 1.6, 2.3)])])
    result, store = build(tmp_path, payload)
    assert result.quarantine.height == 0 and result.counts["incomplete_sibling_quotes"] == 0
    events, quotes = build_odds_tables(store, TheOddsApiNormalizer())  # type: ignore[arg-type]
    assert events.equals(result.events) and quotes.equals(result.quotes)
    assert quotes.height == 2 and (quotes["quality_flags"] == "").all()


def test_reprocessing_is_idempotent_and_raw_capture_immutable(tmp_path: Path) -> None:
    result, store = build(tmp_path, body(two_events()))
    raw = next((store.root / "bodies").rglob("*.json"))
    before = hashlib.sha256(raw.read_bytes()).hexdigest()
    log_before = store.log_path.read_bytes()
    again = build_odds_tables_with_quarantine(store, TheOddsApiNormalizer())  # type: ignore[arg-type]
    assert again.quotes.equals(result.quotes) and again.quarantine.equals(result.quarantine)
    assert again.counts == result.counts
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == before == store.records()[0].sha256
    assert store.log_path.read_bytes() == log_before


def test_fully_invalid_capture_gives_empty_canonical_quotes(tmp_path: Path) -> None:
    last = SNAP - timedelta(minutes=5)
    payload = body([event("ev1", "Alpha One", "Beta One", START, [("bookx", last, 1.0, 0.5)])])
    result, _ = build(tmp_path, payload)
    assert result.quotes.height == 0 and result.events.height == 1
    validate_odds_table("odds_quotes", result.quotes)  # empty frame still matches the schema
    assert result.quarantine.height == 2 and set(result.quarantine["reason_code"]) == {
        "price_not_above_one"
    }
    out = select_quotes(result.quotes, decisions_from_schedule(result.events, 60))
    assert out["status"].to_list() == ["no_quote_before_cutoff"]


def test_malformed_and_non_finite_prices(tmp_path: Path) -> None:
    last = SNAP - timedelta(minutes=5)
    ev = event(
        "ev1",
        "Alpha One",
        "Beta One",
        START,
        [
            ("b1", last, 1.5, 2.6),
            ("b2", last, 1.5, 2.6),
            ("b3", last, 1.5, 2.6),
            ("b4", last, 1.5, 2.6),
            ("b5", last, 1.5, 2.6),
        ],
    )
    books = {b["key"]: b for b in ev["bookmakers"]}
    books["b1"]["markets"][0]["outcomes"][0]["price"] = float("nan")
    books["b2"]["markets"][0]["outcomes"][0]["price"] = float("inf")
    books["b3"]["markets"][0]["outcomes"][0]["price"] = "1.80"  # string: not coerced
    del books["b4"]["markets"][0]["outcomes"][0]["price"]
    books["b5"]["markets"][0]["outcomes"][0]["price"] = True  # bool is not a price
    result, _ = build(tmp_path, body([ev]))
    reasons = dict(
        zip(result.quarantine["bookmaker_key"], result.quarantine["reason_code"], strict=True)
    )
    assert reasons == {
        "b1": "price_non_finite",
        "b2": "price_non_finite",
        "b3": "price_not_numeric",
        "b4": "price_missing",
        "b5": "price_not_numeric",
    }
    assert (
        result.quotes.height == 5
    )  # the five valid sibling prices remain, each flagged incomplete
    assert result.quotes["quality_flags"].str.contains("market_incomplete_after_quarantine").all()
    assert result.quarantine["quote_id"].n_unique() == 5


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ({"price": 1.01}, None),
        ({"price": 1.005}, None),
        ({"price": 2}, None),
        ({"price": 1.0}, "price_not_above_one"),
        ({"price": 0}, "price_not_above_one"),
        ({"price": -3.0}, "price_not_above_one"),
        ({"price": float("nan")}, "price_non_finite"),
        ({"price": float("-inf")}, "price_non_finite"),
        ({"price": "2.0"}, "price_not_numeric"),
        ({"price": [2.0]}, "price_not_numeric"),
        ({"price": False}, "price_not_numeric"),
        ({"price": None}, "price_missing"),
        ({}, "price_missing"),
    ],
)
def test_parse_decimal_price(outcome: dict[str, Any], expected: str | None) -> None:
    value, raw, issue = parse_decimal_price(outcome)
    assert issue == expected and isinstance(raw, str)
    if expected is None:
        assert value > 1.0
