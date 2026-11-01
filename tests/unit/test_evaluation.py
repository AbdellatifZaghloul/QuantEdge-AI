"""Metrics, recalibration, block bootstrap, ranking baseline, fold protocol and registry."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from quantedge.core.chronology import earliest_instant, latest_instant
from quantedge.core.splits import Fold
from quantedge.evaluation.bootstrap import block_bootstrap_means
from quantedge.evaluation.metrics import (
    accuracy_terms,
    apply_slope,
    brier_terms,
    calibration_intercept_slope,
    expected_calibration_error,
    fit_slope_recalibration,
    log_loss_terms,
    reliability_table,
    sigmoid,
)
from quantedge.evaluation.walkforward import Family, evaluate_folds, week_blocks
from quantedge.experiments.registry import LateCheckLimitError, make_run_id, record_late_check_look
from quantedge.models.rank_baseline import RankBaseline, design_matrix, point_in_time_ranks


def test_basic_metrics() -> None:
    y = np.array([1.0, 0.0, 1.0, 0.0])
    p = np.array([0.8, 0.2, 0.5, 0.6])
    assert log_loss_terms(y, p)[0] == pytest.approx(-np.log(0.8))
    assert brier_terms(y, p).tolist() == pytest.approx([0.04, 0.04, 0.25, 0.36])
    assert accuracy_terms(y, p).tolist() == [1.0, 1.0, 0.5, 0.0]
    assert np.isfinite(log_loss_terms(np.array([1.0]), np.array([0.0]))).all()  # clipped


def test_calibration_tools_recover_known_slope() -> None:
    rng = np.random.default_rng(0)
    z = rng.normal(0, 1.5, 50_000)
    y = (rng.random(len(z)) < sigmoid(0.7 * z)).astype(float)
    over_confident = sigmoid(z)
    slope = fit_slope_recalibration(y, over_confident)
    assert slope == pytest.approx(0.7, abs=0.03)
    intercept, fitted = calibration_intercept_slope(y, apply_slope(over_confident, slope))
    assert abs(intercept) < 0.03 and fitted == pytest.approx(1.0, abs=0.03)
    assert expected_calibration_error(y, sigmoid(0.7 * z)) < 0.01
    assert sum(r["n"] for r in reliability_table(y, over_confident, 10)) == len(y)


def test_block_bootstrap_is_seeded_and_brackets_estimate() -> None:
    rng = np.random.default_rng(1)
    blocks = np.repeat(np.arange(200), 10)
    terms = rng.normal(0.6, 0.1, len(blocks))
    a = block_bootstrap_means(blocks, {"x": terms}, replicates=500, seed=7)
    b = block_bootstrap_means(blocks, {"x": terms}, replicates=500, seed=7)
    assert a == b
    assert a["x"]["ci_low"] < a["x"]["estimate"] < a["x"]["ci_high"]
    flat = block_bootstrap_means(blocks, {"c": np.full(len(blocks), 0.3)}, replicates=100, seed=1)
    assert flat["c"]["ci_low"] == pytest.approx(0.3) and flat["c"]["ci_high"] == pytest.approx(0.3)


def test_week_blocks() -> None:
    labels = week_blocks(pl.Series([date(2023, 1, 2), date(2023, 1, 8), date(2023, 1, 9)]))
    assert labels[0] == labels[1] != labels[2]


# ----------------------------------------------------------------------------- ranking baseline


def test_point_in_time_ranks_exclude_same_day_and_stale() -> None:
    monday = date(2023, 1, 9)
    targets = pl.DataFrame(
        {
            "match_key": ["m1", "m2"],
            "player_a_id": ["p", "p"],
            "player_b_id": ["q", "q"],
            "prediction_cutoff_utc": [earliest_instant(monday), earliest_instant(date(2024, 6, 3))],
        }
    )
    rankings = pl.DataFrame(
        {
            "player_id": ["p", "p", "q"],
            "rank": [20, 5, 50],
            "knowable_at_utc": [
                latest_instant(date(2023, 1, 2)),
                latest_instant(monday),
                latest_instant(date(2023, 1, 2)),
            ],
        }
    )
    ranks = point_in_time_ranks(targets, rankings, staleness_days=365).sort("match_key")
    assert ranks.row(0) == ("m1", 20, 50)  # the Monday ranking is not yet knowable
    assert ranks.row(1) == ("m2", None, None)  # > 365 days old -> unranked


def test_rank_baseline_antisymmetric_and_sensible() -> None:
    rng = np.random.default_rng(2)
    n = 4000
    ra, rb = rng.integers(1, 500, n), rng.integers(1, 500, n)
    y = (rng.random(n) < sigmoid(-0.8 * (np.log(ra) - np.log(rb)))).astype(float)
    frame = pl.DataFrame({"rank_a": ra, "rank_b": rb})
    model = RankBaseline().fit(frame, y)
    assert model.coef is not None and model.coef[0] < 0
    swapped = pl.DataFrame({"rank_a": rb, "rank_b": ra})
    assert np.allclose(model.predict(frame) + model.predict(swapped), 1.0)
    unranked = pl.DataFrame({"rank_a": [None, 10.0], "rank_b": [10.0, None]})
    assert np.allclose(design_matrix(unranked)[0], -design_matrix(unranked)[1])


# ----------------------------------------------------------------------------- fold protocol


def test_selection_never_uses_the_test_year() -> None:
    rows = []
    rng = np.random.default_rng(3)
    for year in (2010, 2011, 2012):
        for i in range(400):
            y = int(rng.random() < 0.5)
            honest = 0.7 if y else 0.3
            # "oracle" is perfect only in the test year and terrible before it.
            oracle = (0.99 if y else 0.01) if year == 2012 else (0.01 if y else 0.99)
            rows.append(
                {
                    "match_key": f"{year}-{i}",
                    "tournament_start_date": date(year, 6, 1),
                    "eval_eligible": True,
                    "y": y,
                    "rank_a": 10.0 + y,
                    "rank_b": 20.0,
                    "honest": honest,
                    "oracle": oracle,
                }
            )
    frame = pl.DataFrame(rows)
    fold = Fold(
        "inner_2012",
        "inner",
        date(2010, 1, 1),
        date(2010, 12, 31),
        date(2011, 1, 1),
        date(2011, 12, 31),
        date(2012, 1, 1),
        date(2012, 12, 31),
    )
    test, records = evaluate_folds(frame, (Family("m", ("honest", "oracle")),), [fold])
    assert records[0]["m"]["selected"] == "honest"
    assert test["match_key"].str.starts_with("2012").all() and test.height == 400
    assert set(test.columns) >= {"p_m", "p_m_recal", "p_rank", "p_rank_recal", "p_coin"}


# ----------------------------------------------------------------------------- registry


def test_run_ids_and_late_check_cap(tmp_path: Path) -> None:
    assert make_run_id({"a": 1, "b": [1, 2]}) == make_run_id({"b": [1, 2], "a": 1})
    assert make_run_id({"a": 1}) != make_run_id({"a": 2})
    for expected in (1, 2, 3):
        assert record_late_check_look(tmp_path, "v1", 3, "note") == expected
    with pytest.raises(LateCheckLimitError):
        record_late_check_look(tmp_path, "v1", 3, "fourth")
    assert record_late_check_look(tmp_path, "v2", 3, "other version") == 1
