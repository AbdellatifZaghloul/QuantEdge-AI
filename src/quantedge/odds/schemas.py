"""Canonical, provider-independent odds tables.

Timestamps (all UTC):

- ``commence_time_utc``: provider's scheduled start for the event, as seen in that snapshot.
- ``market_last_update_utc``: provider's *source* timestamp — the last time the provider saw this
  market's prices at the bookmaker (The Odds API: market-level ``last_update``).
- ``observed_at_utc``: when the quote is known to have been displayed: the provider snapshot time
  for historical data, our capture time for live data. **This is the only time used for
  point-in-time admissibility.**
- ``captured_at_utc``: when we downloaded it.

A quote is an *observed* price. Nothing in these tables implies a stake would have been accepted
at that price (``price_semantics`` makes this explicit on every row).
"""

from __future__ import annotations

import pandera.polars as pa
import polars as pl

from quantedge.canonical.schemas import Col

UTC_DT = pl.Datetime("us", "UTC")
EVENT_ID = r"^oe1_[0-9a-f]{32}$"
QUOTE_ID = r"^oq1_[0-9a-f]{32}$"

MARKETS = ("match_winner", "total_games", "game_handicap", "other")
SELECTION_ROLES = ("participant", "over", "under", "other")
CAPTURE_MODES = ("live", "historical")
STREAMS = ("prospective", "historical")
TIMING_CLASSES = ("timestamped", "untimed_near_close")
PRICE_SEMANTICS = "observed_quote_not_guaranteed_executable"
LINK_STATUSES = (
    "linked",
    "ambiguous",
    "unmatched",
    "player_unresolved",
    "time_inconsistent",
    "tournament_unverified",
    "duplicate_provider_event",
)

QUALITY_FLAGS = (
    "odds_out_of_range",  # price <= 1.01 or >= 101
    "source_after_observation",  # market_last_update later than observed_at
    "observed_after_commence",  # in-play or late snapshot
    "commence_time_changed",  # event rescheduled across snapshots
    "unknown_market",
    "snapshot_after_request",  # historical snapshot stamped later than the time requested
    "source_time_missing",  # no market-level source timestamp (age unknown)
)


def _isin(values: tuple[str, ...]) -> tuple[pa.Check, ...]:
    return (pa.Check.isin(list(values)),)


def _regex(pattern: str) -> tuple[pa.Check, ...]:
    return (pa.Check.str_matches(pattern),)


ODDS_TABLES: dict[str, tuple[Col, ...]] = {
    "odds_events": (
        Col("event_id", pl.String(), unique=True, checks=_regex(EVENT_ID)),
        Col("provider", pl.String()),
        Col("provider_event_id", pl.String()),
        Col("competition_key", pl.String()),
        Col("competition_title", pl.String(), nullable=True),
        Col("participant_1", pl.String()),
        Col("participant_2", pl.String()),
        Col("commence_time_utc", UTC_DT),
        Col("commence_time_earliest_utc", UTC_DT),
        Col("first_observed_utc", UTC_DT),
        Col("last_observed_utc", UTC_DT),
        Col("commence_time_versions", pl.Int32()),
        Col("stream", pl.String(), checks=_isin(STREAMS)),
        Col("licence_class", pl.String()),
    ),
    "odds_quotes": (
        Col("quote_id", pl.String(), unique=True, checks=_regex(QUOTE_ID)),
        Col("event_id", pl.String(), checks=_regex(EVENT_ID)),
        Col("provider", pl.String()),
        Col("bookmaker_key", pl.String()),
        Col("bookmaker_title", pl.String(), nullable=True),
        Col("region", pl.String(), nullable=True),
        Col("market", pl.String(), checks=_isin(MARKETS)),
        Col("provider_market_key", pl.String()),
        Col("selection_name", pl.String()),
        Col("selection_role", pl.String(), checks=_isin(SELECTION_ROLES)),
        Col("line", pl.Float64(), nullable=True),
        Col("decimal_odds", pl.Float64(), checks=(pa.Check.gt(1.0),)),
        Col("commence_time_utc", UTC_DT),
        Col("market_last_update_utc", UTC_DT, nullable=True),
        Col("observed_at_utc", UTC_DT),
        Col("captured_at_utc", UTC_DT),
        Col("capture_mode", pl.String(), checks=_isin(CAPTURE_MODES)),
        Col("stream", pl.String(), checks=_isin(STREAMS)),
        Col("timing_class", pl.String(), checks=_isin(TIMING_CLASSES)),
        Col("quality_flags", pl.String()),
        Col("price_semantics", pl.String(), checks=_isin((PRICE_SEMANTICS,))),
        Col("licence_class", pl.String()),
        Col("snapshot_sha256", pl.String()),
    ),
    "event_links": (
        Col("event_id", pl.String(), unique=True, checks=_regex(EVENT_ID)),
        Col("status", pl.String(), checks=_isin(LINK_STATUSES)),
        Col("reason", pl.String()),
        Col("match_key", pl.String(), nullable=True),
        Col("player_1_id", pl.String(), nullable=True),
        Col("player_2_id", pl.String(), nullable=True),
        Col("player_1_method", pl.String()),
        Col("player_2_method", pl.String()),
        Col("participant_1_side", pl.String(), nullable=True, checks=_isin(("A", "B"))),
        Col("candidate_count", pl.Int32()),
        Col("commence_within_day_bounds", pl.Boolean(), nullable=True),
    ),
}


def odds_pandera_schema(table: str) -> pa.DataFrameSchema:
    columns = {
        c.name: pa.Column(c.dtype, checks=list(c.checks), nullable=c.nullable, unique=c.unique)
        for c in ODDS_TABLES[table]
    }
    return pa.DataFrameSchema(columns, strict=True)


def validate_odds_table(table: str, frame: pl.DataFrame) -> pl.DataFrame:
    ordered = frame.select([c.name for c in ODDS_TABLES[table]]).cast(
        {c.name: c.dtype for c in ODDS_TABLES[table]}
    )
    validated: pl.DataFrame = odds_pandera_schema(table).validate(ordered, lazy=True)
    return validated


def empty_table(table: str) -> pl.DataFrame:
    return pl.DataFrame(schema={c.name: c.dtype for c in ODDS_TABLES[table]})
