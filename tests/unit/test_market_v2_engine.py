"""Model-vs-market engine: synthetic data only; outcome-blind steps and leakage guards."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import polars as pl
import pytest

from quantedge.evaluation import market_v2 as mv

RULES = mv.MarketRules()
T0 = datetime(2021, 8, 30, 14, 0, tzinfo=UTC)


def population(n: int = 40) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "match_key": [f"m{i}" for i in range(n)],
            "event_id": [f"e{i}" for i in range(n)],
            "decision_utc": [T0 + timedelta(days=i % 7) for i in range(n)],
            "tournament_start_date": [
                date(2021, 8, 30) + timedelta(days=7 * (i % 4)) for i in range(n)
            ],
            "eval_eligible": [True] * n,
            "identity": ["unique"] * n,
            "status": ["in_population"] * n,
            "started_before_decision": [False] * n,
            "prediction_cutoff_utc": [T0 - timedelta(days=1)] * n,
        }
    )


def quotes(pop: pl.DataFrame, books: tuple[str, ...] = ("pinnacle", "b1", "b2")) -> pl.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    for r in pop.iter_rows(named=True):
        p = float(rng.uniform(0.2, 0.8))
        for k, b in enumerate(books):
            m = 1.04 + 0.01 * k
            at = r["decision_utc"] - timedelta(minutes=5)
            rows.append(
                {
                    "event_id": r["event_id"],
                    "bookmaker_key": b,
                    "observed_at_utc": at,
                    "source_time_utc": at - timedelta(minutes=2),
                    "odds_a": 1 / (p * m),
                    "odds_b": 1 / ((1 - p) * m),
                }
            )
    return pl.DataFrame(rows)


def pipeline(pop: pl.DataFrame, q: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, int], Any]:
    eligible, _ = mv.eligible_population(pop)
    ok, _ = mv.admissible_quotes(q, eligible, RULES)
    priced = mv.devig_quotes(ok, RULES)
    ref = mv.choose_reference(priced, eligible.height, RULES)
    bench = mv.market_benchmarks(priced, ref["bookmaker"], RULES)
    model = pop.select("match_key", pl.lit(0.5).alias("p_model"))
    aligned, report = mv.align(eligible, model, bench, "p_model")
    return aligned, report, ref


def _empty_bench() -> pl.DataFrame:
    return pl.DataFrame({"match_key": []}, schema={"match_key": pl.String})


def test_population_statuses_are_applied_unchanged() -> None:
    pop = population(6).with_columns(
        pl.Series("status", ["in_population", "short_lead"] + ["in_population"] * 4),
        pl.Series("identity", ["unique", "unique", "identity_conflict"] + ["unique"] * 3),
        pl.Series("started_before_decision", [False] * 3 + [True, False, False]),
        pl.Series("eval_eligible", [True] * 4 + [False, True]),
    )
    pop = pop.with_columns(
        pl.when(pl.col("match_key") == "m5")
        .then(pl.col("decision_utc") + timedelta(hours=1))
        .otherwise(pl.col("prediction_cutoff_utc"))
        .alias("prediction_cutoff_utc")
    )
    eligible, counts = mv.eligible_population(pop)
    assert eligible["match_key"].to_list() == ["m0"]
    assert dict(counts.iter_rows()) == {
        "eligible": 1,
        "identity_conflict": 1,
        "market_before_model_cutoff": 1,
        "not_gate0_eligible": 1,
        "short_lead": 1,
        "started_before_decision": 1,
    }


def test_quotes_after_decision_stale_or_missing_time_are_rejected() -> None:
    pop = population(1)
    d = pop["decision_utc"][0]
    q = pl.DataFrame(
        {
            "event_id": ["e0"] * 6,
            "bookmaker_key": ["late", "old", "nosrc", "future_src", "bad", "ok"],
            "observed_at_utc": [d + timedelta(minutes=1), d - timedelta(minutes=20), d, d, d, d],
            "source_time_utc": [
                d,
                d - timedelta(minutes=20),
                None,
                d + timedelta(seconds=1),
                d,
                d - timedelta(minutes=29),
            ],
            "odds_a": [1.9, 1.9, 1.9, 1.9, 1.0, 1.9],
            "odds_b": [1.9] * 6,
        }
    )
    ok, counts = mv.admissible_quotes(q, pop, RULES)
    assert ok["bookmaker_key"].to_list() == ["ok"]
    assert dict(counts.iter_rows()) == {
        "admissible": 1,
        "after_decision": 2,
        "invalid_price": 1,
        "missing_source_time": 1,
        "outside_snapshot_window": 1,
    }
    stale = q.filter(pl.col("bookmaker_key") == "ok").with_columns(
        (pl.col("observed_at_utc") - timedelta(minutes=31)).alias("source_time_utc")
    )
    assert dict(mv.admissible_quotes(stale, pop, RULES)[1].iter_rows()) == {"stale": 1}


def test_post_decision_quotes_cannot_change_any_benchmark() -> None:
    pop = population()
    q = quotes(pop)
    base, _, _ = pipeline(pop, q)
    late = q.with_columns(
        (pl.col("observed_at_utc") + timedelta(minutes=10)).alias("observed_at_utc"),
        (pl.col("odds_a") * 3).alias("odds_a"),
    )
    combined, _, _ = pipeline(pop, pl.concat([q, late]))
    assert base.sort("match_key").equals(combined.sort("match_key"))


def test_devig_reference_and_consensus() -> None:
    pop = population()
    aligned, report, ref = pipeline(pop, quotes(pop))
    assert ref["bookmaker"] == "pinnacle" and ref["rule"] == "preferred_adequate"
    assert report == {
        "eligible": 40,
        "missing_model": 0,
        "missing_reference": 0,
        "missing_consensus": 0,
    }
    p = aligned["p_market_reference"].to_numpy()
    assert np.all((p > 0.19) & (p < 0.81))


def test_reference_fallback_and_clone_collapse() -> None:
    pop = population()
    even = pl.col("event_id").str.slice(1).cast(pl.Int32) % 2 == 0
    q = quotes(pop).filter(~((pl.col("bookmaker_key") == "pinnacle") & even))
    clone = q.filter(pl.col("bookmaker_key") == "b1").with_columns(
        pl.lit("a_clone").alias("bookmaker_key")
    )
    _, _, ref = pipeline(pop, pl.concat([q, clone]))
    assert ref["rule"] == "fallback_highest_availability_clones_collapsed"
    assert ref["bookmaker"] == "a_clone" and "b1" not in ref["availability"]
    _, report, _ = pipeline(pop, quotes(pop, ("only",)))
    assert report["missing_consensus"] == 40  # one distinct source < 2


def test_outcome_columns_are_refused_before_scoring() -> None:
    pop = population()
    with pytest.raises(mv.OutcomeLeakError):
        mv.eligible_population(pop.with_columns(pl.lit(1).alias("y")))
    with pytest.raises(mv.OutcomeLeakError):
        mv.admissible_quotes(quotes(pop).with_columns(pl.lit("x").alias("winner_id")), pop, RULES)
    eligible, _ = mv.eligible_population(pop)
    leaky = pop.select("match_key", pl.lit(0.5).alias("p"), pl.lit(1).alias("a_won"))
    with pytest.raises(mv.OutcomeLeakError):
        mv.align(eligible, leaky, _empty_bench(), "p")


def test_scoring_needs_approval_and_respects_holdout() -> None:
    pop = population()
    aligned, _, _ = pipeline(pop, quotes(pop))
    outcomes = pop.select("match_key", pl.lit(1, pl.Int8).alias("y"))
    kw: dict[str, Any] = {"rules": RULES, "replicates": 50, "seed": 1}
    with pytest.raises(mv.OutcomeLeakError, match="approval"):
        mv.score(aligned, outcomes, approve_outcome_access=False, **kw)
    late = aligned.with_columns(pl.lit(date(2025, 1, 6)).alias("tournament_start_date"))
    with pytest.raises(mv.OutcomeLeakError, match="holdout"):
        mv.score(late, outcomes, approve_outcome_access=True, **kw)
    with pytest.raises(ValueError, match="without an outcome"):
        mv.score(aligned, outcomes.slice(1), approve_outcome_access=True, **kw)


def test_outcomes_never_change_the_aligned_population() -> None:
    pop = population()
    aligned, _, _ = pipeline(pop, quotes(pop))
    fp = mv.outcome_blind_fingerprint(aligned)
    rng = np.random.default_rng(1)
    for flip in (0, 1):
        y = pop.select("match_key", pl.Series("y", (rng.random(40) < 0.5).astype(np.int8) ^ flip))
        out = mv.score(aligned, y, approve_outcome_access=True, rules=RULES, replicates=50, seed=1)
        assert out["contrasts"]["p_market_reference"]["n"] == 40
        assert mv.outcome_blind_fingerprint(aligned) == fp


def test_paired_scoring_is_correct_on_synthetic_outcomes() -> None:
    pop = population()
    aligned, _, _ = pipeline(pop, quotes(pop))
    outcomes = aligned.select(
        "match_key", (pl.col("p_market_reference") > 0.5).cast(pl.Int8).alias("y")
    )
    out = mv.score(
        aligned, outcomes, approve_outcome_access=True, rules=RULES, replicates=200, seed=3
    )
    c = out["contrasts"]["p_market_reference"]
    assert out["layer"] == "probability_quality"
    assert c["log_loss_diff"]["estimate"] > 0  # a coin-flip model loses to an informative market
    yy = outcomes["y"].to_numpy().astype(float)
    pm = aligned["p_market_reference"].to_numpy()
    assert c["brier_diff"]["estimate"] == pytest.approx(np.mean((0.5 - yy) ** 2 - (pm - yy) ** 2))


def test_price_report_is_never_executable() -> None:
    pop = population()
    eligible, _ = mv.eligible_population(pop)
    ok, _ = mv.admissible_quotes(quotes(pop), eligible, RULES)
    rep = mv.price_report(mv.devig_quotes(ok, RULES), RULES)
    assert rep["executable"] is False and rep["why_not_executable"]
    assert 0.03 < rep["overround_quantiles"][1] < 0.07
    grid = rep["cost_grid_effective_booksum_median"]
    assert grid["0.0"] < grid["0.03"]


def test_duplicate_claims_and_predictions_are_refused() -> None:
    pop = population(3)
    with pytest.raises(ValueError, match="more than one"):
        mv.eligible_population(pl.concat([pop, pop.head(1)]))
    eligible, _ = mv.eligible_population(pop)
    model = pop.select("match_key", pl.lit(0.5).alias("p"))
    with pytest.raises(ValueError, match="duplicate model"):
        mv.align(eligible, pl.concat([model, model.head(1)]), _empty_bench(), "p")


def test_adapter_orients_odds_to_player_a() -> None:
    t = T0
    selected = pl.DataFrame(
        {
            "event_id": ["e0", "e1", "e2"],
            "bookmaker_key": ["b"] * 3,
            "observed_at_utc": [t] * 3,
            "source_time": [t] * 3,
            "odds_1": [1.5, 1.5, 1.5],
            "odds_2": [2.6, 2.6, 2.6],
            "selection_1_is_participant_1": [True, False, True],
            "status": ["available", "available", "stale"],
        }
    )
    links = pl.DataFrame(
        {
            "event_id": ["e0", "e1", "e2"],
            "participant_1_side": ["A", "A", "B"],
            "status": ["linked"] * 3,
        }
    )
    out = mv.oriented_quotes(selected, links).sort("event_id")
    assert out["odds_a"].to_list() == [1.5, 2.6]
    assert set(mv.QUOTE_COLUMNS) == set(out.columns)
