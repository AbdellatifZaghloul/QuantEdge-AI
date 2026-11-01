"""Pre-registered pilot comparison: Phase 2 Elo vs bookmaker probabilities (protocol v1).

Order of operations (outcome-blind until step 6):

1. population: Gate 0 eval-eligible Grand Slam main-draw matches inside the protocol window and
   the provider's documented coverage, restricted to matches with frozen Phase 2 predictions;
2. linked provider events for that population (all other link statuses are reported);
3. decisions at earliest provider start minus the pre-registered lead;
4. strict point-in-time selection at the primary staleness limit;
5. bookmaker availability and the outcome-blind reference choice;
6. join outcomes and score every pre-registered contrast on the identical population.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
import polars as pl

from quantedge.odds.baseline import (
    attach_participant_order,
    compare_on_identical_population,
    market_probabilities,
)
from quantedge.odds.coverage import load_coverage_table
from quantedge.odds.preregistration import (
    OUTCOME_COLUMNS,
    Protocol,
    ProtocolViolationError,
    assert_within_window,
    choose_reference,
)
from quantedge.odds.selector import SelectorConfig, decisions_from_schedule, select_quotes


def pilot_population(
    matches: pl.DataFrame,
    tournaments: pl.DataFrame,
    eligibility: pl.DataFrame,
    predictions: pl.DataFrame,
    protocol: Protocol,
    tournament_names: dict[str, str],
) -> pl.DataFrame:
    """Population rows (``match_key``, ``tournament_start_date``, ``sport_key``) — no outcomes."""
    pop = protocol.population
    coverage = {
        r["sport_key"]: datetime.fromisoformat(
            r["historical_from_utc"].replace("Z", "+00:00")
        ).date()
        for r in load_coverage_table()
    }
    frame = matches.join(tournaments.select("tournament_id", "name", "level"), on="tournament_id")
    frame = frame.join(eligibility.select("match_key", "eval_eligible"), on="match_key")
    frame = frame.filter(
        pl.col("level").is_in(list(pop.canonical_levels))
        & pl.col("tournament_start_date").is_between(
            pop.tournament_start_from, pop.tournament_start_to
        )
    )
    if pop.main_draw_only:
        frame = frame.filter(pl.col("draw_category").str.ends_with("_main"))
    if pop.require_gate0_eval_eligible:
        frame = frame.filter(pl.col("eval_eligible"))
    parts = []
    for key in pop.sport_keys:
        pattern = tournament_names[key]
        parts.append(
            frame.filter(
                pl.col("name").str.contains(pattern) & (pl.col("match_day_hi") >= coverage[key])
            ).with_columns(pl.lit(key).alias("sport_key"))
        )
    population = pl.concat(parts).select("match_key", "tournament_start_date", "sport_key")
    population = population.join(predictions.select("match_key"), on="match_key", how="semi")
    return population.unique("match_key").sort("match_key")


def _availability(selected: pl.DataFrame, linked_matches: int) -> pl.DataFrame:
    per_book = (
        selected.filter(pl.col("status") == "available")
        .group_by("bookmaker_key")
        .agg(pl.col("match_key").n_unique().alias("matches_available"))
    )
    return per_book.with_columns(
        (pl.col("matches_available") / max(linked_matches, 1)).alias("availability")
    ).sort("bookmaker_key")


def _market_columns(
    selected: pl.DataFrame, links: pl.DataFrame, protocol: Protocol, reference: str
) -> pl.DataFrame:
    methods = (protocol.market.devig_primary, *protocol.market.devig_secondary)
    probs = market_probabilities(selected, links, methods=methods)
    if probs.height == 0:
        return pl.DataFrame(schema={"match_key": pl.String})
    usable = probs.filter(pl.col("usable"))
    primary = usable.filter(pl.col("bookmaker_key") == reference).select(
        "match_key",
        *[pl.col(f"p_{m}_a").alias(f"market_primary_{m}") for m in methods],
    )
    method = protocol.market.devig_primary
    consensus = (
        usable.group_by("match_key")
        .agg(
            pl.col(f"p_{method}_a").median().alias("market_consensus"),
            pl.len().alias("consensus_bookmakers"),
        )
        .filter(pl.col("consensus_bookmakers") >= protocol.market.consensus_min_bookmakers)
    )
    return primary.join(consensus, on="match_key", how="full", coalesce=True).with_columns(
        pl.col(f"market_primary_{method}").alias("market_primary")
    )


def run_pilot(
    *,
    protocol: Protocol,
    protocol_sha256: str,
    population: pl.DataFrame,
    events: pl.DataFrame,
    quotes: pl.DataFrame,
    links: pl.DataFrame,
    predictions: pl.DataFrame,
    holdout_start: date,
) -> dict[str, Any]:
    """Execute protocol v1. ``predictions`` carries ``y`` and the model columns (frozen run)."""
    assert_within_window(population, protocol, holdout_start=holdout_start)
    timing = protocol.timing
    linked = links.filter(pl.col("status") == protocol.population.require_link_status).join(
        population.select("match_key"), on="match_key", how="semi"
    )
    pop_events = events.join(linked.select("event_id"), on="event_id", how="semi")
    decisions = decisions_from_schedule(pop_events, timing.decision_lead_minutes)

    def select(staleness: float) -> pl.DataFrame:
        config = SelectorConfig(
            max_staleness_minutes=staleness,
            market=protocol.market.market,
            missing_source_time=timing.missing_source_time,
        )
        chosen = attach_participant_order(select_quotes(quotes, decisions, config), events)
        return chosen.join(linked.select("event_id", "match_key"), on="event_id", how="left")

    primary_sel = select(timing.primary_max_staleness_minutes)
    outcome_blind_guard(primary_sel)
    availability = _availability(primary_sel, linked["match_key"].n_unique())
    outcome_blind_guard(availability)
    reference, rule = choose_reference(availability, protocol)  # outcome-blind (step 5)

    model = protocol.model_predictions
    base = population.join(
        predictions.select(
            "match_key",
            "y",
            pl.col(model.elo_column).alias("elo"),
            pl.col(model.rank_column).alias("rank"),
        ),
        on="match_key",
    )
    assert_within_window(base, protocol, holdout_start=holdout_start)

    def score(selection: pl.DataFrame, columns: list[str]) -> dict[str, Any]:
        market = _market_columns(selection, linked, protocol, reference)
        frame = base.join(market, on="match_key", how="left")
        return compare_on_identical_population(
            frame,
            columns,
            holdout_start=holdout_start,
            replicates=protocol.metrics.bootstrap_replicates,
            seed=protocol.metrics.bootstrap_seed,
        )

    primary_columns = ["market_primary", "elo", "rank", "market_consensus"]
    main = score(primary_sel, primary_columns)
    sensitivity_staleness = {
        f"{s:g}": score(select(s), primary_columns)
        for s in timing.sensitivity_max_staleness_minutes
    }
    devig_columns = [f"market_primary_{m}" for m in protocol.market.devig_secondary]
    sensitivity_devig = score(primary_sel, ["market_primary", "elo", *devig_columns])

    ref_rows = primary_sel.filter(
        (pl.col("bookmaker_key") == reference) & pl.col("status").is_in(["available", "stale"])
    )
    ages = ref_rows["staleness_minutes"].drop_nulls().to_numpy()
    return {
        "protocol_id": protocol.protocol_id,
        "protocol_sha256": protocol_sha256,
        "information_asymmetry_note": protocol.conduct.information_asymmetry_note,
        "price_semantics": "observed quotes; consensus is a probability summary, not a price",
        "reference": {"bookmaker": reference, "rule": rule},
        "sample_sizes": {
            "population": population.height,
            "linked_events": linked.height,
            "population_without_linked_event": population.height - linked["match_key"].n_unique(),
            "scored_identical_population": main["n_common"],
        },
        "link_status_counts": links.group_by("status").len().sort("status").to_dicts(),
        "selection_status_counts": primary_sel.group_by("status").len().sort("status").to_dicts(),
        "bookmaker_availability": availability.to_dicts(),
        "quote_age_minutes_reference": {q: float(np.quantile(ages, q)) for q in (0.1, 0.5, 0.9)}
        if ages.size
        else {},
        "primary": main,
        "sensitivity_staleness": sensitivity_staleness,
        "sensitivity_devig": sensitivity_devig,
    }


def outcome_blind_guard(frame: pl.DataFrame) -> None:
    """Assert that a pre-outcome stage frame carries no outcome columns."""
    leaked = OUTCOME_COLUMNS & set(frame.columns)
    if leaked:
        raise ProtocolViolationError(f"outcome columns present before step 6: {sorted(leaked)}")
