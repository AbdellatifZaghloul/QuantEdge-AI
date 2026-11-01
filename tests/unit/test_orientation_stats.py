"""AT-12 (CI part): orientation statistics have power against a leaky assignment."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl

from quantedge.quality.orientation import (
    ALPHA,
    binomial_two_sided,
    holm,
    orientation_checks,
    stratified_g_test,
)


def _synthetic(
    n: int, p_a_wins: float, seed: int
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    rng = np.random.default_rng(seed)
    a_won = rng.random(n) < p_a_wins
    years = rng.integers(2005, 2025, n)
    keys = [f"m1_{i:032x}" for i in range(n)]
    matches = pl.DataFrame(
        {
            "match_key": keys,
            "tournament_id": [f"t:{y}" for y in years],
            "tournament_start_date": [date(int(y), 6, 1) for y in years],
            "outcome_type": ["completed"] * n,
            "a_won": a_won,
        }
    )
    tournaments = pl.DataFrame(
        {"tournament_id": [f"t:{y}" for y in range(2005, 2025)], "level": ["A"] * 20}
    )
    ranks_a = rng.integers(1, 500, n)
    ranks_b = rng.integers(1, 500, n)
    entries = pl.concat(
        [
            pl.DataFrame({"match_key": keys, "side": ["A"] * n, "rank_at_event_start": ranks_a}),
            pl.DataFrame({"match_key": keys, "side": ["B"] * n, "rank_at_event_start": ranks_b}),
        ]
    )
    return matches, entries, tournaments


def test_fair_orientation_passes() -> None:
    result = orientation_checks(*_synthetic(10_000, 0.5, seed=1))
    assert result["passed"], result


def test_leaky_orientation_fails_global_test() -> None:
    """Mutation test: 'winner is A' in 55% of 10,000 matches must be detected."""
    result = orientation_checks(*_synthetic(10_000, 0.55, seed=2))
    global_test = next(t for t in result["tests"] if t["name"] == "global_binomial_a_won")
    assert global_test["p_value"] < ALPHA
    assert not result["passed"]


def test_statistical_helpers() -> None:
    assert binomial_two_sided(5000, 10_000) > 0.9
    assert binomial_two_sided(5500, 10_000) < 1e-10
    g, df, p = stratified_g_test([(50, 100), (100, 200)])
    assert (round(g, 9), df) == (0.0, 2) and p == 1.0
    assert holm([0.01, 0.04, 0.03]) == [0.03, 0.06, 0.06]
