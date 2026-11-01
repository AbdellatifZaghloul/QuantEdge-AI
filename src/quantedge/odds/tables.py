"""Build canonical ``odds_events`` and ``odds_quotes`` tables from a capture store."""

from __future__ import annotations

from collections import defaultdict
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


def build_odds_tables(
    store: CaptureStore, provider: OddsProvider
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Deterministic normalisation of every verified capture (re-runnable; store is untouched)."""
    licence = str(provider.licence_class)
    quote_rows: dict[str, dict[str, Any]] = {}
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
            eid = event_id(provider.name, q.provider_event_id)
            qid = quote_id(
                provider.name,
                q.provider_event_id,
                q.bookmaker_key,
                q.provider_market_key,
                q.selection_name,
                "" if q.line is None else repr(q.line),
                repr(q.decimal_odds),
                "" if q.market_last_update_utc is None else q.market_last_update_utc.isoformat(),
                snapshot.observed_at_utc.isoformat(),
            )
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
    quotes = pl.DataFrame(list(quote_rows.values())) if quote_rows else None
    if quotes is None:
        return events, empty_table("odds_quotes")
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
    return events, validate_odds_table("odds_quotes", quotes).sort("quote_id")
