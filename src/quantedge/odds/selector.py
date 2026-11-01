"""Strict point-in-time selection of two-way match-winner quotes.

For each decision (event, decision time ``t``) and each bookmaker, the selector returns the most
recent *complete* two-way quote such that:

1. ``timing_class == "timestamped"`` (untimed near-close quotes are never admissible);
2. ``observed_at_utc <= t`` (nothing observed after the decision time, so no future closing odds);
3. ``observed_at_utc < commence_time_utc`` (pre-match only) and ``t < commence_time_utc``;
4. both selections come from the same bookmaker *and the same observation*;
5. the market's source timestamp is no older than ``max_staleness`` at ``t``.

Every decision gets a row per bookmaker and an explicit ``status``. Prices are labelled as observed
quotes; nothing here asserts that a stake would have been accepted.
"""

from __future__ import annotations

from datetime import timedelta

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
)


class SelectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_staleness_minutes: float = 30.0
    market: str = "match_winner"


def select_quotes(
    quotes: pl.DataFrame, decisions: pl.DataFrame, config: SelectorConfig | None = None
) -> pl.DataFrame:
    """``decisions``: ``event_id``, ``decision_time_utc``. Returns one row per decision x bookmaker
    (or one row per decision with ``bookmaker_key = null`` when no bookmaker qualifies)."""
    config = config or SelectorConfig()
    market = quotes.filter(pl.col("market") == config.market)
    joined = decisions.select("event_id", "decision_time_utc").join(
        market, on="event_id", how="left"
    )
    timed = pl.col("timing_class") == "timestamped"
    admissible = joined.filter(
        timed
        & (pl.col("observed_at_utc") <= pl.col("decision_time_utc"))
        & (pl.col("observed_at_utc") < pl.col("commence_time_utc"))
        & (pl.col("decision_time_utc") < pl.col("commence_time_utc"))
    )
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
    per_decision = joined.group_by("event_id", "decision_time_utc").agg(
        pl.col("quote_id").is_not_null().any().alias("any_quote"),
        (pl.col("timing_class") == "timestamped").any().alias("any_timed"),
        pl.col("commence_time_utc").min().alias("commence"),
        (pl.col("observed_at_utc") <= pl.col("decision_time_utc")).any().alias("any_before"),
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
        .when(pl.col("any_before").fill_null(False))
        .then(pl.lit("incomplete_market"))
        .otherwise(pl.lit("no_quote_before_cutoff"))
        .alias("status"),
    )
    out = pl.concat([selected, reasons], how="diagonal_relaxed")
    return out.with_columns(pl.lit(PRICE_SEMANTICS).alias("price_semantics")).sort(
        "event_id", "decision_time_utc", "bookmaker_key", nulls_last=True
    )


def decisions_from_schedule(events: pl.DataFrame, lead_minutes: float = 60.0) -> pl.DataFrame:
    """Market decision time = provider scheduled start minus a lead (ARCHITECTURE.md §5.3 rule 2).

    Model features keep their own (earlier, day-bound) cutoff, so in any model-vs-market comparison
    the market is given at least as much information as the model.
    """
    return events.select(
        "event_id",
        (pl.col("commence_time_utc") - timedelta(minutes=lead_minutes)).alias("decision_time_utc"),
    )
