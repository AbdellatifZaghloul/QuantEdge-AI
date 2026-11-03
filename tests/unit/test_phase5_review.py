"""Exploratory Phase 5 review helpers: format-aware Elo and feature masking (synthetic only)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from quantedge.core.splits import Fold
from quantedge.experiments.phase5_review import elo_fold_predictions, masked
from quantedge.models.format_elo import (
    best_of_five,
    best_of_three,
    format_adjusted,
    set_probability,
)
from quantedge.models.gbm import FEATURES


def test_set_probability_inverts_best_of_three() -> None:
    s = np.linspace(0.01, 0.99, 99)
    np.testing.assert_allclose(set_probability(best_of_three(s)), s, atol=1e-12)
    assert best_of_three(np.array([0.6]))[0] == pytest.approx(0.648)
    assert best_of_five(np.array([0.6]))[0] == pytest.approx(0.68256)


def test_best_of_five_is_more_decisive_and_antisymmetric() -> None:
    p3 = np.linspace(0.02, 0.98, 49)
    p5 = format_adjusted(p3, np.full(p3.shape, 5))
    assert np.all(np.abs(p5 - 0.5) >= np.abs(p3 - 0.5) - 1e-12)
    np.testing.assert_allclose(p5 + format_adjusted(1 - p3, np.full(p3.shape, 5)), 1.0, atol=1e-12)
    assert format_adjusted(np.array([0.5]), np.array([5]))[0] == pytest.approx(0.5)


def test_only_best_of_five_matches_are_adjusted() -> None:
    p = np.array([0.7, 0.7, 0.7])
    out = format_adjusted(p, np.array([3, 5, 3]))
    assert out[0] == out[2] == 0.7 and out[1] > 0.7


def test_masking_rejects_unknown_features_and_blanks_known_ones() -> None:
    frame = pl.DataFrame({f: [1.0, 2.0] for f in FEATURES})
    out = masked(frame, ["serve_diff"])
    assert out["serve_diff"].null_count() == 2 and out["elo_diff"].to_list() == [1.0, 2.0]
    with pytest.raises(ValueError, match="unknown features"):
        masked(frame, ["not_a_feature"])


def test_elo_diagnostic_never_uses_test_year_labels() -> None:
    rng = np.random.default_rng(0)
    n = 1200
    years = rng.integers(2012, 2015, size=n)
    frame = pl.DataFrame(
        {
            "match_key": [f"m{i}" for i in range(n)],
            "tournament_start_date": [
                date(int(y), 1, 1) + timedelta(days=int(d))
                for y, d in zip(years, rng.integers(0, 300, size=n), strict=True)
            ],
            "eval_eligible": [True] * n,
            "elo_diff": rng.normal(0, 150, size=n),
            "best_of": rng.choice([3, 5], size=n),
            "y": rng.integers(0, 2, size=n).astype(np.int8),
        }
    )
    fold = Fold(
        "inner_2014", "inner", date(2005, 1, 1), date(2012, 12, 31),
        date(2013, 1, 1), date(2013, 12, 31), date(2014, 1, 1), date(2014, 12, 31),
    )  # fmt: skip
    a = elo_fold_predictions(frame, [fold], formatted=True)
    flipped = frame.with_columns(
        pl.when(pl.col("tournament_start_date").dt.year() == 2014)
        .then(1 - pl.col("y"))
        .otherwise(pl.col("y"))
        .alias("y")
    )
    b = elo_fold_predictions(flipped, [fold], formatted=True)
    assert a.equals(b) and a.height == int((years == 2014).sum())
