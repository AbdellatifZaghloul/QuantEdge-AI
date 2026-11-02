"""Build canonical ``odds_events`` and ``odds_quotes`` tables from a capture store.

Structurally invalid prices (missing, non-numeric, non-finite, or <= 1.0) never enter
``odds_quotes`` and are never repaired: they go to a separate ``odds_quote_quarantine`` table with a
reason code and full provenance. Valid sibling selections of an affected market stay in
``odds_quotes`` but carry ``market_incomplete_after_quarantine``; the selector then finds only one
selection for that (bookmaker, observation) and reports ``incomplete_market``, so no probability can
be formed from a half market. The raw capture store is only read, never modified.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import polars as pl

from quantedge.core.hashing import sha256_fields
from quantedge.odds.provider import OddsProvider
from quantedge.odds.schemas import PRICE_SEMANTICS, empty_table, validate_odds_table
from quantedge.odds.store import CaptureStore


def event_id(provider: str, provider_event_id: str) -> str:
    return "oe1_" + sha256_fields("qe.odds_event.v1", provider, provider_event_id)[:32]


def quote_id(*fields: str) -> str:
    return "oq1_" + sha256_fields("qe.odds_quote.v1", *fields)[:32]


def _flags(
    odds: float,
    last_update: datetime | None,
    observed: datetime,
    commence: datetime,
    market: str,
    requested: datetime | None = None,
) -> str:
    flags = []
    if requested is not None and observed > requested:
        flags.append("snapshot_after_request")
    if last_update is None:
        flags.append("source_time_missing")
    if odds <= 1.01 or odds >= 101:
        flags.append("odds_out_of_range")
    if last_update is not None and last_update > observed:
        flags.append("source_after_observation")
    if observed >= commence:
        flags.append("observed_after_commence")
    if market == "other":
        flags.append("unknown_market")
    return ",".join(flags)


@dataclass(frozen=True)
class OddsBuild:
    events: pl.DataFrame
    quotes: pl.DataFrame
    quarantine: pl.DataFrame
    counts: dict[str, Any] = field(default_factory=dict)


def build_odds_tables(
    store: CaptureStore, provider: OddsProvider
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Valid canonical events and quotes (see ``build_odds_tables_with_quarantine``)."""
    result = build_odds_tables_with_quarantine(store, provider)
    return result.events, result.quotes


def build_odds_tables_with_quarantine(store: CaptureStore, provider: OddsProvider) -> OddsBuild:
    """Deterministic normalisation of every verified capture (re-runnable; store is untouched)."""
    licence = str(provider.licence_class)
    quote_rows: dict[str, dict[str, Any]] = {}
    quarantine_rows: dict[str, dict[str, Any]] = {}
    raw_quotes = 0
    seen: dict[str, dict[str, Any]] = {}
    commence_versions: dict[str, set[datetime]] = defaultdict(set)
    for record, capture in store.iter_captures():
        if not record.endpoint.endswith("/odds"):
            continue
        snapshot = provider.normalize(capture)
        requested = (
            datetime.fromisoformat(record.snapshot_requested_utc.replace("Z", "+00:00"))
            if record.snapshot_requested_utc
            else None
        )
        for event in snapshot.events:
            eid = event_id(provider.name, event.provider_event_id)
            commence_versions[eid].add(event.commence_time_utc)
            row = seen.get(eid)
            if row is None or snapshot.observed_at_utc >= row["last_observed_utc"]:
                first = row["first_observed_utc"] if row else snapshot.observed_at_utc
                seen[eid] = {
                    "event_id": eid,
                    "provider": provider.name,
                    "provider_event_id": event.provider_event_id,
                    "competition_key": event.competition_key,
                    "competition_title": event.competition_title,
                    "participant_1": event.participant_1,
                    "participant_2": event.participant_2,
                    "commence_time_utc": event.commence_time_utc,
                    "first_observed_utc": min(first, snapshot.observed_at_utc),
                    "last_observed_utc": snapshot.observed_at_utc,
                    "stream": store.stream,
                    "licence_class": licence,
                }
            else:
                row["first_observed_utc"] = min(row["first_observed_utc"], snapshot.observed_at_utc)
        for q in snapshot.quotes:
            raw_quotes += 1
            eid = event_id(provider.name, q.provider_event_id)
            # Valid quotes keep their historical ID (repr of the float price); invalid ones are
            # keyed on the provider's raw value so non-numeric inputs stay distinct and auditable.
            price_key = repr(q.decimal_odds) if q.price_issue is None else f"raw:{q.price_raw}"
            qid = quote_id(
                provider.name,
                q.provider_event_id,
                q.bookmaker_key,
                q.provider_market_key,
                q.selection_name,
                "" if q.line is None else repr(q.line),
                price_key,
                "" if q.market_last_update_utc is None else q.market_last_update_utc.isoformat(),
                snapshot.observed_at_utc.isoformat(),
            )
            if q.price_issue is not None:
                quarantine_rows[qid] = {
                    "quote_id": qid,
                    "reason_code": q.price_issue,
                    "event_id": eid,
                    "provider": provider.name,
                    "bookmaker_key": q.bookmaker_key,
                    "region": q.region,
                    "market": q.market,
                    "provider_market_key": q.provider_market_key,
                    "selection_name": q.selection_name,
                    "line": q.line,
                    "price_raw": q.price_raw if q.price_raw is not None else "null",
                    "decimal_odds": q.decimal_odds,
                    "commence_time_utc": q.commence_time_utc,
                    "market_last_update_utc": q.market_last_update_utc,
                    "observed_at_utc": snapshot.observed_at_utc,
                    "captured_at_utc": snapshot.captured_at_utc,
                    "capture_mode": snapshot.capture_mode,
                    "stream": store.stream,
                    "licence_class": licence,
                    "snapshot_sha256": record.sha256,
                }
                continue
            quote_rows[qid] = {
                "quote_id": qid,
                "event_id": eid,
                "provider": provider.name,
                "bookmaker_key": q.bookmaker_key,
                "bookmaker_title": q.bookmaker_title,
                "region": q.region,
                "market": q.market,
                "provider_market_key": q.provider_market_key,
                "selection_name": q.selection_name,
                "selection_role": q.selection_role,
                "line": q.line,
                "decimal_odds": q.decimal_odds,
                "commence_time_utc": q.commence_time_utc,
                "market_last_update_utc": q.market_last_update_utc,
                "observed_at_utc": snapshot.observed_at_utc,
                "captured_at_utc": snapshot.captured_at_utc,
                "capture_mode": snapshot.capture_mode,
                "stream": store.stream,
                "timing_class": "timestamped",
                "quality_flags": _flags(
                    q.decimal_odds,
                    q.market_last_update_utc,
                    snapshot.observed_at_utc,
                    q.commence_time_utc,
                    q.market,
                    requested,
                ),
                "price_semantics": PRICE_SEMANTICS,
                "licence_class": licence,
                "snapshot_sha256": record.sha256,
            }
    for eid, row in seen.items():
        row["commence_time_versions"] = len(commence_versions[eid])
        row["commence_time_earliest_utc"] = min(commence_versions[eid])
    events = (
        validate_odds_table("odds_events", pl.DataFrame(list(seen.values())))
        if seen
        else empty_table("odds_events")
    ).sort("event_id")
    quarantine = (
        validate_odds_table("odds_quote_quarantine", pl.DataFrame(list(quarantine_rows.values())))
        if quarantine_rows
        else empty_table("odds_quote_quarantine")
    ).sort("quote_id")
    counts: dict[str, Any] = {
        "raw_quotes": raw_quotes,
        "distinct_quotes": len(quote_rows) + len(quarantine_rows),
        "valid_quotes": len(quote_rows),
        "quarantined_quotes": len(quarantine_rows),
        "quarantined_by_reason": dict(
            sorted(Counter(r["reason_code"] for r in quarantine_rows.values()).items())
        ),
        "events": events.height,
    }
    if not quote_rows:
        return OddsBuild(
            events,
            empty_table("odds_quotes"),
            quarantine,
            counts | {"incomplete_sibling_quotes": 0},
        )
    quotes = pl.DataFrame(list(quote_rows.values()))
    # Flag surviving siblings of quarantined selections (same market observation, same |line|).
    market_keys = [
        "event_id",
        "bookmaker_key",
        "provider_market_key",
        "observed_at_utc",
        "_abs_line",
    ]
    affected = (
        quarantine.with_columns(pl.col("line").cast(pl.Float64).abs().alias("_abs_line"))
        .select(market_keys)
        .unique()
    )
    quotes = quotes.with_columns(pl.col("line").cast(pl.Float64).abs().alias("_abs_line"))
    hit = quotes.join(affected, on=market_keys, how="semi", nulls_equal=True)["quote_id"]
    quotes = quotes.with_columns(
        pl.when(pl.col("quote_id").is_in(hit.to_list()))
        .then(
            pl.concat_str(
                [pl.col("quality_flags"), pl.lit("market_incomplete_after_quarantine")],
                separator=",",
            ).str.strip_chars(",")
        )
        .otherwise(pl.col("quality_flags"))
        .alias("quality_flags")
    ).drop("_abs_line")
    counts["incomplete_sibling_quotes"] = hit.len()
    changed = events.filter(pl.col("commence_time_versions") > 1)["event_id"]
    quotes = quotes.with_columns(
        pl.when(pl.col("event_id").is_in(changed.to_list()))
        .then(
            pl.concat_str(
                [pl.col("quality_flags"), pl.lit("commence_time_changed")], separator=","
            ).str.strip_chars(",")
        )
        .otherwise(pl.col("quality_flags"))
        .alias("quality_flags")
    )
    return OddsBuild(
        events, validate_odds_table("odds_quotes", quotes).sort("quote_id"), quarantine, counts
    )
