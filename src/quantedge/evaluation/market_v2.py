"""Model-versus-market evaluation engine on the frozen ``market-benchmark-v2`` population.

Engine version ``model-vs-market-v2-engine.v1`` (draft; config
``configs/preregistration/model_vs_market_v2_engine.yaml``). Everything before :func:`score` is
outcome-blind. The V2 population, decision times, identity and eligibility rules are inputs produced
by the frozen V2 code (``odds/v2_draft.py``); this module never redefines them.

Three layers are kept separate and labelled:

1. **probability quality**: paired log-loss / Brier, model vs de-vigged market reference;
2. **theoretical market benchmark**: de-vigged reference book and median consensus of distinct
   sources (not prices anyone could bet);
3. **executable prices**: *not available* from historical snapshots (no stake limits, liquidity,
   account restrictions or latency). Only margins, best quoted prices and a cost grid are reported.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import polars as pl

from quantedge.evaluation.bootstrap import block_bootstrap_means
from quantedge.evaluation.metrics import brier_terms, log_loss_terms, summary
from quantedge.evaluation.walkforward import week_blocks
from quantedge.odds.devig import DevigError, Method, devig

ENGINE_VERSION = "model-vs-market-v2-engine.v1"
OUTCOME_COLUMNS = frozenset({"y", "a_won", "winner_id", "outcome_type", "score_raw"})
POPULATION_COLUMNS = (
    "match_key",
    "event_id",
    "decision_utc",
    "tournament_start_date",
    "eval_eligible",
    "identity",
    "status",
    "started_before_decision",
    "prediction_cutoff_utc",
)
QUOTE_COLUMNS = (
    "event_id",
    "bookmaker_key",
    "observed_at_utc",
    "source_time_utc",
    "odds_a",
    "odds_b",
)


class OutcomeLeakError(PermissionError):
    """Outcome data reached an outcome-blind step, or scoring was not authorized."""


@dataclass(frozen=True)
class MarketRules:
    """Frozen V2 market rules (values from ``market_benchmark_v2.yaml``; never tuned here)."""

    snapshot_window_minutes: float = 10.0
    freshness_minutes: float = 30.0
    reference_bookmaker: str = "pinnacle"
    reference_min_availability: float = 0.80
    consensus_min_distinct_sources: int = 2
    devig: Method = "proportional"
    secondary_devig: tuple[Method, ...] = ("power", "shin")
    holdout_start: date = date(2025, 1, 1)
    cost_grid: tuple[float, ...] = (0.0, 0.01, 0.02, 0.03)


def _refuse_outcomes(frame: pl.DataFrame, name: str) -> None:
    leaked = OUTCOME_COLUMNS & set(frame.columns)
    if leaked:
        raise OutcomeLeakError(f"{name} carries outcome columns {sorted(leaked)} before scoring")


def _require(frame: pl.DataFrame, columns: Sequence[str], name: str) -> None:
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise ValueError(f"{name} lacks columns {missing}")


# ------------------------------------------------------------------- outcome-blind: population


def eligible_population(population: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Apply the frozen V2 statuses unchanged; return (eligible, exclusion counts)."""
    _require(population, POPULATION_COLUMNS, "population")
    _refuse_outcomes(population, "population")
    if (
        population["match_key"].drop_nulls().n_unique()
        != population["match_key"].drop_nulls().len()
    ):
        raise ValueError("population has a match claimed by more than one decision")
    reason = (
        pl.when(pl.col("status") != "in_population")
        .then(pl.col("status"))
        .when(pl.col("identity") != "unique")
        .then(pl.col("identity"))
        .when(pl.col("started_before_decision"))
        .then(pl.lit("started_before_decision"))
        .when(~pl.col("eval_eligible").fill_null(False))
        .then(pl.lit("not_gate0_eligible"))
        .when(pl.col("prediction_cutoff_utc") > pl.col("decision_utc"))
        .then(pl.lit("market_before_model_cutoff"))
        .otherwise(pl.lit("eligible"))
    )
    tagged = population.with_columns(reason.alias("reason"))
    counts = tagged.group_by("reason").len().sort("reason")
    return tagged.filter(pl.col("reason") == "eligible").drop("reason"), counts


# ----------------------------------------------------------------------- outcome-blind: quotes


def admissible_quotes(
    quotes: pl.DataFrame, eligible: pl.DataFrame, rules: MarketRules
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Strict point-in-time quote filter; every rejected quote keeps a reason."""
    _require(quotes, QUOTE_COLUMNS, "quotes")
    _refuse_outcomes(quotes, "quotes")
    window = timedelta(minutes=rules.snapshot_window_minutes)
    fresh = timedelta(minutes=rules.freshness_minutes)
    joined = quotes.join(eligible.select("event_id", "match_key", "decision_utc"), on="event_id")
    age = pl.col("decision_utc") - pl.col("source_time_utc")
    finite = pl.col("odds_a").is_finite() & pl.col("odds_b").is_finite()
    reason = (
        pl.when(pl.col("observed_at_utc") > pl.col("decision_utc"))
        .then(pl.lit("after_decision"))
        .when(pl.col("observed_at_utc") <= pl.col("decision_utc") - window)
        .then(pl.lit("outside_snapshot_window"))
        .when(pl.col("source_time_utc").is_null())
        .then(pl.lit("missing_source_time"))
        .when(pl.col("source_time_utc") > pl.col("decision_utc"))
        .then(pl.lit("after_decision"))
        .when(age > fresh)
        .then(pl.lit("stale"))
        .when(~finite.fill_null(False) | (pl.col("odds_a") <= 1) | (pl.col("odds_b") <= 1))
        .then(pl.lit("invalid_price"))
        .otherwise(pl.lit("admissible"))
    )
    tagged = joined.with_columns(reason.alias("reason"))
    counts = tagged.group_by("reason").len().sort("reason")
    ok = tagged.filter(pl.col("reason") == "admissible").drop("reason")
    # One quote per (event, bookmaker): the latest observation at or before the decision.
    ok = ok.sort("observed_at_utc").group_by("event_id", "bookmaker_key").agg(pl.all().last())
    return ok, counts


def devig_quotes(quotes: pl.DataFrame, rules: MarketRules) -> pl.DataFrame:
    methods = (rules.devig, *rules.secondary_devig)
    rows = []
    for r in quotes.iter_rows(named=True):
        out = {k: r[k] for k in ("event_id", "match_key", "bookmaker_key", "odds_a", "odds_b")}
        out["booksum"] = 1 / r["odds_a"] + 1 / r["odds_b"]
        for m in methods:
            try:
                out[f"p_{m}_a"] = devig((r["odds_a"], r["odds_b"]), m).probabilities[0]
            except DevigError:
                out[f"p_{m}_a"] = None
        rows.append(out)
    schema = {
        "event_id": pl.String,
        "match_key": pl.String,
        "bookmaker_key": pl.String,
        "odds_a": pl.Float64,
        "odds_b": pl.Float64,
        "booksum": pl.Float64,
    } | {f"p_{m}_a": pl.Float64 for m in methods}
    return pl.DataFrame(rows, schema=schema)


def _collapse_clones(priced: pl.DataFrame) -> pl.DataFrame:
    """Identical price pair at the same decision counts once (alphabetically first book kept)."""
    keyed = priced.with_columns(
        pl.col("odds_a").round(3).alias("_pa"), pl.col("odds_b").round(3).alias("_pb")
    )
    return (
        keyed.sort("bookmaker_key")
        .unique(subset=["event_id", "_pa", "_pb"], keep="first", maintain_order=True)
        .drop("_pa", "_pb")
    )


def choose_reference(priced: pl.DataFrame, n_events: int, rules: MarketRules) -> dict[str, Any]:
    """Outcome-blind: preferred book if adequate, else highest availability, clones collapsed."""
    if n_events == 0:
        return {"bookmaker": None, "rule": "no_events", "availability": {}}
    distinct = _collapse_clones(priced)
    avail = (
        distinct.group_by("bookmaker_key")
        .agg(pl.col("event_id").n_unique().alias("n"))
        .with_columns((pl.col("n") / n_events).alias("share"))
        .sort(["share", "bookmaker_key"], descending=[True, False])
    )
    shares = dict(zip(avail["bookmaker_key"].to_list(), avail["share"].to_list(), strict=True))
    preferred = shares.get(rules.reference_bookmaker, 0.0)
    if preferred >= rules.reference_min_availability:
        return {
            "bookmaker": rules.reference_bookmaker,
            "rule": "preferred_adequate",
            "availability": shares,
        }
    best = avail["bookmaker_key"][0] if avail.height else None
    return {
        "bookmaker": best,
        "rule": "fallback_highest_availability_clones_collapsed",
        "availability": shares,
    }


def market_benchmarks(
    priced: pl.DataFrame, reference: str | None, rules: MarketRules
) -> pl.DataFrame:
    """Per match: reference-book and consensus (median of distinct sources) probabilities."""
    col = f"p_{rules.devig}_a"
    usable = priced.filter(pl.col(col).is_not_null())
    ref = usable.filter(pl.col("bookmaker_key") == reference).select(
        "match_key", pl.col(col).alias("p_market_reference")
    )
    cons = (
        _collapse_clones(usable)
        .group_by("match_key")
        .agg(pl.col(col).median().alias("p_market_consensus"), pl.len().alias("distinct_sources"))
        .with_columns(
            pl.when(pl.col("distinct_sources") >= rules.consensus_min_distinct_sources)
            .then(pl.col("p_market_consensus"))
            .otherwise(None)
            .alias("p_market_consensus")
        )
    )
    return cons.join(ref, on="match_key", how="full", coalesce=True)


def align(
    eligible: pl.DataFrame, model: pl.DataFrame, benchmarks: pl.DataFrame, model_column: str
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Exact identity alignment: one model probability and one market row per eligible match."""
    _refuse_outcomes(model, "model predictions")
    _require(model, ("match_key", model_column), "model predictions")
    if model["match_key"].n_unique() != model.height:
        raise ValueError("duplicate model predictions per match")
    out = (
        eligible.select("match_key", "event_id", "decision_utc", "tournament_start_date")
        .join(
            model.select("match_key", pl.col(model_column).alias("p_model")),
            on="match_key",
            how="left",
        )
        .join(benchmarks, on="match_key", how="left")
    )
    report = {
        "eligible": eligible.height,
        "missing_model": int(out["p_model"].null_count()),
        "missing_reference": int(out["p_market_reference"].null_count())
        if "p_market_reference" in out.columns
        else out.height,
        "missing_consensus": int(out["p_market_consensus"].null_count())
        if "p_market_consensus" in out.columns
        else out.height,
    }
    return out, report


def price_report(priced: pl.DataFrame, rules: MarketRules) -> dict[str, Any]:
    """Margins and best quoted prices. Theoretical only: nothing here is an executable price."""
    if priced.height == 0:
        return {"executable": False, "n_quotes": 0}
    over = priced["booksum"] - 1
    best = priced.group_by("match_key").agg(pl.col("odds_a").max(), pl.col("odds_b").max())
    best_sum = 1 / best["odds_a"] + 1 / best["odds_b"]
    return {
        "executable": False,
        "why_not_executable": [
            "historical snapshot prices; no stake limits, liquidity or account restrictions",
            "decision-to-bet latency and slippage unobserved",
            "best price across books assumes simultaneous accounts at every book",
        ],
        "n_quotes": priced.height,
        "overround_quantiles": [float(over.quantile(q) or 0) for q in (0.0, 0.5, 0.9, 1.0)],
        "overround_by_book": {
            k: float(v)
            for k, v in priced.group_by("bookmaker_key")
            .agg((pl.col("booksum") - 1).median())
            .iter_rows()
        },
        "best_price_booksum_median": float(np.median(best_sum.to_numpy())),
        "best_price_underround_matches": int((best_sum < 1).sum()),
        "cost_grid_effective_booksum_median": {
            str(c): float(np.median(best_sum.to_numpy() / (1 - c))) for c in rules.cost_grid
        },
    }


# ----------------------------------------------------------------------- outcome-dependent


def score(
    aligned: pl.DataFrame,
    outcomes: pl.DataFrame,
    *,
    approve_outcome_access: bool,
    rules: MarketRules,
    replicates: int,
    seed: int,
    level: float = 0.95,
) -> dict[str, Any]:
    """Paired comparisons on rows where every compared probability exists. Needs approval."""
    if not approve_outcome_access:
        raise OutcomeLeakError("outcome-based model-vs-market scoring requires explicit approval")
    _refuse_outcomes(aligned, "aligned frame")
    if aligned.height and aligned["tournament_start_date"].max() >= rules.holdout_start:  # type: ignore[operator]
        raise OutcomeLeakError("aligned frame reaches the sealed holdout")
    if outcomes["match_key"].n_unique() != outcomes.height:
        raise ValueError("duplicate outcomes per match")
    frame = aligned.join(outcomes.select("match_key", "y"), on="match_key", how="left")
    if frame["y"].null_count():
        raise ValueError("eligible matches without an outcome")
    out: dict[str, Any] = {
        "engine": ENGINE_VERSION,
        "layer": "probability_quality",
        "contrasts": {},
    }
    for market in ("p_market_reference", "p_market_consensus"):
        rows = frame.filter(pl.col("p_model").is_not_null() & pl.col(market).is_not_null())
        if rows.height == 0:
            out["contrasts"][market] = {"n": 0}
            continue
        y = rows["y"].to_numpy().astype(float)
        pm, pk = rows["p_model"].to_numpy(), rows[market].to_numpy()
        terms = {
            "log_loss_diff": log_loss_terms(y, pm) - log_loss_terms(y, pk),
            "brier_diff": brier_terms(y, pm) - brier_terms(y, pk),
        }
        ci = block_bootstrap_means(
            week_blocks(rows["tournament_start_date"]),
            terms,
            replicates=replicates,
            seed=seed,
            level=level,
        )
        out["contrasts"][market] = {
            "n": rows.height,
            "model": summary(y, pm),
            "market": summary(y, pk),
            **ci,
        }
    return out


def outcome_blind_fingerprint(aligned: pl.DataFrame) -> str:
    """SHA-256 of the aligned population, fixed before any outcome is joined."""
    return hashlib.sha256(aligned.sort("match_key").write_csv().encode()).hexdigest()


def oriented_quotes(selected: pl.DataFrame, links: pl.DataFrame) -> pl.DataFrame:
    """Adapter: frozen-V2 selector output (after ``attach_participant_order``) to engine quotes.

    ``links`` is ``v2_draft.links_for_market`` output (unique identities only). Odds are oriented to
    canonical player A exactly as ``odds.baseline.market_probabilities`` does.
    """
    _refuse_outcomes(selected, "selected quotes")
    frame = selected.filter(pl.col("status") == "available").join(
        links.filter(pl.col("status") == "linked").select("event_id", "participant_1_side"),
        on="event_id",
    )
    first_is_a = pl.col("selection_1_is_participant_1") == (pl.col("participant_1_side") == "A")
    return frame.select(
        "event_id",
        "bookmaker_key",
        "observed_at_utc",
        pl.col("source_time").alias("source_time_utc"),
        pl.when(first_is_a).then(pl.col("odds_1")).otherwise(pl.col("odds_2")).alias("odds_a"),
        pl.when(first_is_a).then(pl.col("odds_2")).otherwise(pl.col("odds_1")).alias("odds_b"),
    )
