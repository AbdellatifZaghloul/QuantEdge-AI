"""Strict point-in-time selection of two-way match-winner quotes.

For each decision (event, decision time ``t``) and each bookmaker, the selector returns the most
recent *complete* two-way quote whose every row passes all of these checks:

1. ``timing_class == "timestamped"`` (untimed near-close quotes are never admissible);
2. ``observed_at_utc <= t`` (nothing observed after the decision: no future closing odds);
3. ``observed_at_utc < commence_time_utc`` and ``t < commence_time_utc`` (pre-match only);
4. no blocking timing flag (e.g. a historical snapshot later than the time requested, or a source
   timestamp later than the observation);
5. source timestamp (``market_last_update_utc``) not later than the observation **nor** the decision
   (clock-inconsistent rows are quarantined, never repaired);
6. a missing source timestamp is rejected by default (``missing_source_time="reject"``), because the
   quote's age cannot be established;
7. both selections come from the same bookmaker *and the same observation*.

A selected quote older than ``max_staleness_minutes`` (decision time minus source time) is returned
with status ``stale``. Decisions without a usable quote get an explicit status explaining why.
Prices are observed quotes; nothing here asserts that a stake would have been accepted.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

import polars as pl
from pydantic import BaseModel, ConfigDict

from quantedge.odds.schemas import PRICE_SEMANTICS

STATUSES = (
    "available",
    "stale",
    "no_quote_before_cutoff",
    "untimed_not_admissible",
    "decision_after_commence",
    "incomplete_market",
    "quarantined_timestamp",
    "source_time_missing",
)
BLOCKING_FLAGS = ("source_after_observation", "observed_after_commence", "snapshot_after_request")


class SelectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_staleness_minutes: float = 30.0
    market: str = "match_winner"
    missing_source_time: Literal["reject", "use_observed"] = "reject"
    blocking_flags: tuple[str, ...] = BLOCKING_FLAGS


def _block_reason(config: SelectorConfig) -> pl.Expr:
    flags = pl.col("quality_flags").fill_null("").str.split(",")
    blocked_flag = flags.list.eval(pl.element().is_in(list(config.blocking_flags))).list.any()
    source = pl.col("market_last_update_utc")
    clock_bad = (source > pl.col("observed_at_utc")) | (source > pl.col("decision_time_utc"))
    reason = (
        pl.when(pl.col("quote_id").is_null())
        .then(pl.lit("no_quote"))
        .when(pl.col("timing_class") != "timestamped")
        .then(pl.lit("untimed"))
        .when(pl.col("observed_at_utc") > pl.col("decision_time_utc"))
        .then(pl.lit("after_cutoff"))
        .when(
            (pl.col("observed_at_utc") >= pl.col("commence_time_utc"))
            | (pl.col("decision_time_utc") >= pl.col("commence_time_utc"))
        )
        .then(pl.lit("in_play"))
        .when(blocked_flag.fill_null(False) | clock_bad.fill_null(False))
        .then(pl.lit("quarantined"))
    )
    if config.missing_source_time == "reject":
        reason = reason.when(source.is_null()).then(pl.lit("source_missing"))
    return reason.otherwise(pl.lit(None, dtype=pl.String))


def select_quotes(
    quotes: pl.DataFrame, decisions: pl.DataFrame, config: SelectorConfig | None = None
) -> pl.DataFrame:
    """``decisions``: ``event_id``, ``decision_time_utc``. Returns one row per decision x bookmaker
    (or one row per decision with ``bookmaker_key = null`` when no bookmaker qualifies)."""
    config = config or SelectorConfig()
    market = quotes.filter(pl.col("market") == config.market)
    if "quality_flags" not in market.columns:
        market = market.with_columns(pl.lit("").alias("quality_flags"))
    joined = (
        decisions.select("event_id", "decision_time_utc")
        .join(market, on="event_id", how="left")
        .with_columns(_block_reason(config).alias("_block"))
    )
    admissible = joined.filter(pl.col("_block").is_null())
    # Complete two-way observations: two distinct selections in one (bookmaker, observation).
    groups = admissible.group_by(
        "event_id", "decision_time_utc", "bookmaker_key", "observed_at_utc"
    ).agg(
        pl.col("selection_name").n_unique().alias("n_sel"),
        pl.len().alias("n_rows"),
        pl.col("selection_name"),
        pl.col("decimal_odds"),
        pl.col("market_last_update_utc").min().alias("source_time"),
        pl.col("commence_time_utc").first(),
        pl.col("quote_id"),
    )
    complete = groups.filter((pl.col("n_sel") == 2) & (pl.col("n_rows") == 2))
    latest = (
        complete.sort("observed_at_utc", descending=True)
        .group_by("event_id", "decision_time_utc", "bookmaker_key", maintain_order=True)
        .first()
    )
    staleness = pl.col("decision_time_utc") - pl.coalesce("source_time", "observed_at_utc")
    selected = latest.with_columns(
        (staleness.dt.total_seconds() / 60.0).alias("staleness_minutes"),
        pl.col("selection_name").list.get(0).alias("selection_1"),
        pl.col("selection_name").list.get(1).alias("selection_2"),
        pl.col("decimal_odds").list.get(0).alias("odds_1"),
        pl.col("decimal_odds").list.get(1).alias("odds_2"),
        pl.col("quote_id").list.get(0).alias("quote_id_1"),
        pl.col("quote_id").list.get(1).alias("quote_id_2"),
    ).with_columns(
        pl.when(staleness > timedelta(minutes=config.max_staleness_minutes))
        .then(pl.lit("stale"))
        .otherwise(pl.lit("available"))
        .alias("status")
    )
    keep = [
        "event_id",
        "decision_time_utc",
        "bookmaker_key",
        "status",
        "observed_at_utc",
        "source_time",
        "staleness_minutes",
        "commence_time_utc",
        "selection_1",
        "odds_1",
        "selection_2",
        "odds_2",
        "quote_id_1",
        "quote_id_2",
    ]
    selected = selected.select(keep)

    # Explicit statuses for decisions without a usable quote.
    covered = selected.select("event_id", "decision_time_utc").unique()
    missing = decisions.select("event_id", "decision_time_utc").join(
        covered, on=["event_id", "decision_time_utc"], how="anti"
    )
    before = pl.col("observed_at_utc") <= pl.col("decision_time_utc")
    per_decision = joined.group_by("event_id", "decision_time_utc").agg(
        pl.col("quote_id").is_not_null().any().alias("any_quote"),
        (pl.col("timing_class") == "timestamped").any().alias("any_timed"),
        pl.col("commence_time_utc").min().alias("commence"),
        pl.col("_block").is_null().any().alias("any_admissible"),
        (before & (pl.col("_block") == "quarantined")).any().alias("any_quarantined"),
        (before & (pl.col("_block") == "source_missing")).any().alias("any_source_missing"),
    )
    reasons = missing.join(per_decision, on=["event_id", "decision_time_utc"], how="left").select(
        "event_id",
        "decision_time_utc",
        pl.when(~pl.col("any_quote").fill_null(False))
        .then(pl.lit("no_quote_before_cutoff"))
        .when(~pl.col("any_timed").fill_null(False))
        .then(pl.lit("untimed_not_admissible"))
        .when(pl.col("decision_time_utc") >= pl.col("commence"))
        .then(pl.lit("decision_after_commence"))
        .when(pl.col("any_admissible").fill_null(False))
        .then(pl.lit("incomplete_market"))
        .when(pl.col("any_quarantined").fill_null(False))
        .then(pl.lit("quarantined_timestamp"))
        .when(pl.col("any_source_missing").fill_null(False))
        .then(pl.lit("source_time_missing"))
        .otherwise(pl.lit("no_quote_before_cutoff"))
        .alias("status"),
    )
    out = pl.concat([selected, reasons], how="diagonal_relaxed")
    return out.with_columns(pl.lit(PRICE_SEMANTICS).alias("price_semantics")).sort(
        "event_id", "decision_time_utc", "bookmaker_key", nulls_last=True
    )


def decisions_from_schedule(events: pl.DataFrame, lead_minutes: float = 60.0) -> pl.DataFrame:
    """Market decision time = provider scheduled start minus a lead (ARCHITECTURE.md §5.3 rule 2).

    The anchor is the *earliest* start time the provider ever showed for the event
    (``commence_time_earliest_utc`` when present): a later reschedule can only be learned later,
    so anchoring on it would let future schedule information move the decision. Model features
    keep their own (earlier, day-bound) cutoff, so the market never has less information than the
    model in a comparison.
    """
    anchor = (
        "commence_time_earliest_utc"
        if "commence_time_earliest_utc" in events.columns
        else "commence_time_utc"
    )
    return events.select(
        "event_id",
        (pl.col(anchor) - timedelta(minutes=lead_minutes)).alias("decision_time_utc"),
    )
