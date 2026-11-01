"""Sample-size-aware orientation checks (VALIDATION.md §5.1).

Orientation is winner-independent by construction; these tests are a sanity net that can detect
an implementation bug that leaks the outcome into the A/B assignment.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import polars as pl
from scipy import stats

ALPHA = 0.001
MIN_STRATUM = 200
Z_999 = 3.2905  # two-sided 99.9% normal quantile


@dataclass(frozen=True)
class TestResult:
    name: str
    n: int
    successes: int
    rate: float
    p_value: float
    holm_adjusted_p: float
    passed: bool
    detail: dict[str, Any]


def binomial_two_sided(successes: int, n: int, p: float = 0.5) -> float:
    if n == 0:
        return 1.0
    return float(stats.binomtest(successes, n, p, alternative="two-sided").pvalue)


def stratified_g_test(strata: Sequence[tuple[int, int]]) -> tuple[float, int, float]:
    """G-test of per-stratum success rates against 0.5.

    ``strata`` holds ``(successes, n)``. Returns ``(G, df, p)`` where df = number of strata.
    """
    g = 0.0
    for k, n in strata:
        expected = n / 2
        for observed in (k, n - k):
            if observed > 0:
                g += 2 * observed * math.log(observed / expected)
    df = len(strata)
    p = float(stats.chi2.sf(g, df)) if df else 1.0
    return g, df, p


def holm(p_values: Sequence[float]) -> list[float]:
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted


def acceptance_half_width(n: int) -> float:
    return Z_999 * 0.5 / math.sqrt(n) if n else float("nan")


def orientation_checks(
    matches: pl.DataFrame, entries: pl.DataFrame, tournaments: pl.DataFrame
) -> dict[str, Any]:
    """Run the three VALIDATION.md §5.1 checks on completed matches of the given frame."""
    completed = matches.filter(pl.col("outcome_type") == "completed")
    n = completed.height
    k = int(completed["a_won"].sum())

    strata_frame = (
        completed.join(tournaments.select("tournament_id", "level"), on="tournament_id")
        .with_columns(
            pl.concat_str(
                [pl.col("tournament_start_date").dt.year().cast(pl.String), pl.col("level")],
                separator="|",
                ignore_nulls=True,
            ).alias("stratum")
        )
        .group_by("stratum")
        .agg(pl.col("a_won").sum().alias("k"), pl.len().alias("n"))
    )
    big = strata_frame.filter(pl.col("n") >= MIN_STRATUM)
    small = strata_frame.filter(pl.col("n") < MIN_STRATUM)
    strata = [(int(a), int(b)) for a, b in big.select("k", "n").iter_rows()]
    if small.height:
        strata.append((int(small["k"].sum()), int(small["n"].sum())))
    g, df, p_strata = stratified_g_test(strata)

    ranks = entries.select("match_key", "side", "rank_at_event_start")
    paired = (
        completed.select("match_key")
        .join(ranks.filter(pl.col("side") == "A").drop("side"), on="match_key")
        .rename({"rank_at_event_start": "rank_a"})
        .join(ranks.filter(pl.col("side") == "B").drop("side"), on="match_key")
        .rename({"rank_at_event_start": "rank_b"})
        .filter(
            pl.col("rank_a").is_not_null()
            & pl.col("rank_b").is_not_null()
            & (pl.col("rank_a") != pl.col("rank_b"))
        )
    )
    n_cov = paired.height
    k_cov = paired.filter(pl.col("rank_a") < pl.col("rank_b")).height

    raw = [
        binomial_two_sided(k, n),
        p_strata,
        binomial_two_sided(k_cov, n_cov),
    ]
    adjusted = holm(raw)
    results = [
        TestResult(
            "global_binomial_a_won", n, k, k / n if n else math.nan, raw[0], adjusted[0],
            raw[0] >= ALPHA, {"acceptance_half_width_99_9": acceptance_half_width(n)},
        ),
        TestResult(
            "stratified_g_year_level", n, k, k / n if n else math.nan, raw[1], adjusted[1],
            raw[1] >= ALPHA,
            {"G": g, "df": df, "strata": len(strata), "pooled_small_strata": small.height},
        ),
        TestResult(
            "covariate_a_higher_ranked", n_cov, k_cov, k_cov / n_cov if n_cov else math.nan,
            raw[2], adjusted[2], raw[2] >= ALPHA,
            {"acceptance_half_width_99_9": acceptance_half_width(n_cov)},
        ),
    ]  # fmt: skip
    return {
        "alpha": ALPHA,
        "family_correction": "holm",
        "passed": all(r.passed for r in results) and all(a >= ALPHA for a in adjusted),
        "tests": [asdict(r) for r in results],
    }
