"""Phase 5 candidate: leakage, future-perturbation, antisymmetry, determinism (synthetic only)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from quantedge.core.splits import Fold, load_split_config
from quantedge.experiments.phase5 import Phase5Config, compare, load_inner_tables
from quantedge.models.gbm import FEATURES, GbmSettings, symmetric_predict, train_fold
from quantedge.models.gbm_features import DIFF_FEATURES, FeatureParams, knowable_sweep, swap
from tests.conftest import REPO_ROOT

PARAMS = FeatureParams(elo_reference={"k_mode": "fivethirtyeight", "k_c": 250})
T0 = datetime(2015, 3, 1, tzinfo=UTC)


def at(days: float) -> datetime:
    return T0 + timedelta(days=days)


def results_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "match_key": pl.String,
            "player_a_id": pl.String,
            "player_b_id": pl.String,
            "a_won": pl.Boolean,
            "knowable_at_utc": pl.Datetime("us", "UTC"),
            "prediction_cutoff_utc": pl.Datetime("us", "UTC"),
            "surface": pl.String,
            "minutes": pl.Int64,
            "p_elo": pl.Float64,
        },
    )


def stats_frame(rows: list[tuple[str, str, int, int, int, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "match_key": mk, "player_id": pid, "serve_points": sp,
                "first_serve_points_won": spw // 2, "second_serve_points_won": spw - spw // 2,
                "return_points_played": rp, "return_points_won": rpw,
                "break_points_faced": 5, "break_points_saved": 3,
            }
            for mk, pid, sp, spw, rp, rpw in rows
        ]
    )  # fmt: skip


TARGET = pl.DataFrame(
    {
        "match_key": ["target"],
        "player_a_id": ["p1"],
        "player_b_id": ["p2"],
        "prediction_cutoff_utc": [at(10)],
        "surface": ["Hard"],
    }
).with_columns(pl.col("prediction_cutoff_utc").cast(pl.Datetime("us", "UTC")))


def base_results(late_minutes: int = 90, late_won: bool = True) -> pl.DataFrame:
    return results_frame(
        [
            # knowable before the target's cutoff
            {"match_key": "past", "player_a_id": "p1", "player_b_id": "p3", "a_won": True,
             "knowable_at_utc": at(5), "prediction_cutoff_utc": at(4), "surface": "Hard",
             "minutes": 120, "p_elo": 0.5},
            # played before the cutoff but only knowable after it: must not count
            {"match_key": "late", "player_a_id": "p1", "player_b_id": "p2", "a_won": late_won,
             "knowable_at_utc": at(11), "prediction_cutoff_utc": at(9), "surface": "Hard",
             "minutes": late_minutes, "p_elo": 0.5},
        ]
    )  # fmt: skip


def stats_rows(late_serve: int = 60) -> pl.DataFrame:
    return stats_frame(
        [("past", "p1", 80, 56, 70, 28), ("past", "p3", 70, 42, 80, 24),
         ("late", "p1", 90, late_serve, 85, 30), ("late", "p2", 85, 50, 90, 30)]
    )  # fmt: skip


def sweep(results: pl.DataFrame, stats: pl.DataFrame) -> dict[str, object]:
    return knowable_sweep(TARGET, results, stats, PARAMS).row(0, named=True)


def test_results_knowable_after_the_cutoff_never_change_features() -> None:
    reference = sweep(base_results(), stats_rows())
    perturbed = sweep(base_results(late_minutes=400, late_won=False), stats_rows(late_serve=5))
    assert perturbed == reference
    assert reference["matches_14_a"] == 1.0 and reference["minutes_14_a"] == 120.0
    assert reference["matches_14_b"] == 0.0  # p2's only match is not yet knowable


def test_knowable_results_do_change_features() -> None:
    reference = sweep(base_results(), stats_rows())
    stats = stats_rows().with_columns(
        pl.when(pl.col("match_key") == "past")
        .then(pl.col("first_serve_points_won") - 20)
        .otherwise(pl.col("first_serve_points_won"))
        .alias("first_serve_points_won")
    )
    changed = sweep(base_results(), stats)
    assert changed["serve_a"] < reference["serve_a"]
    # p2 has no knowable history: its value is the tour-mean prior built from knowable rows only
    # ("past" for p1 and p3; the late match never enters the prior).
    assert reference["serve_b"] == pytest.approx((56 + 42) / (80 + 70))
    assert changed["serve_b"] == pytest.approx((56 - 20 + 42 - 20) / (80 + 70))
    assert changed["stats_points_b"] == reference["stats_points_b"] == 0.0


def test_unknown_players_get_shrunk_priors_not_missing_values() -> None:
    row = sweep(base_results(), stats_rows())
    assert row["stats_points_b"] == 0.0 and 0.0 < row["serve_b"] < 1.0


def test_sweep_is_orientation_consistent() -> None:
    flipped = TARGET.with_columns(
        pl.col("player_b_id").alias("player_a_id"), pl.col("player_a_id").alias("player_b_id")
    )
    a = sweep(base_results(), stats_rows())
    b = knowable_sweep(flipped, base_results(), stats_rows(), PARAMS).row(0, named=True)
    for name in ("serve", "return", "form", "matches_14", "minutes_30"):
        assert a[f"{name}_a"] == b[f"{name}_b"] and a[f"{name}_b"] == b[f"{name}_a"]


# --------------------------------------------------------------------------- model training


def synthetic_frame(seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    n = 3000
    years = rng.integers(2010, 2015, size=n)
    days = rng.integers(0, 300, size=n)
    x = rng.normal(size=(n, len(FEATURES)))
    x[:, FEATURES.index("level_code")] = rng.integers(0, 3, size=n)
    x[:, FEATURES.index("surface_code")] = rng.integers(0, 3, size=n)
    x[:, FEATURES.index("draw_code")] = rng.integers(0, 2, size=n)
    logit = 0.8 * x[:, 0] + 0.3 * x[:, 5]
    y = (rng.random(n) < 1.0 / (1.0 + np.exp(-logit))).astype(np.int8)
    frame = pl.DataFrame({f: x[:, i] for i, f in enumerate(FEATURES)}).with_columns(
        pl.Series("match_key", [f"m{i}" for i in range(n)]),
        pl.Series(
            "tournament_start_date",
            [
                date(int(yy), 1, 1) + timedelta(days=int(d))
                for yy, d in zip(years, days, strict=True)
            ],
        ),
        pl.lit(True).alias("eval_eligible"),
        pl.Series("y", y),
    )
    return frame


FOLD = Fold(
    name="inner_2014", kind="inner",
    fit_start=date(2010, 1, 1), fit_end=date(2012, 12, 31),
    calibration_start=date(2013, 1, 1), calibration_end=date(2013, 12, 31),
    test_start=date(2014, 1, 1), test_end=date(2014, 12, 31),
)  # fmt: skip
SETTINGS = GbmSettings(
    grid=({"num_leaves": 7, "min_data_in_leaf": 20, "learning_rate": 0.1},
          {"num_leaves": 15, "min_data_in_leaf": 40, "learning_rate": 0.1}),
    fixed={"feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1},
    max_rounds=200, early_stopping_rounds=20, seed=7, num_threads=2,
)  # fmt: skip


def test_predictions_are_exactly_antisymmetric() -> None:
    frame = synthetic_frame()
    _, record, booster = train_fold(frame, FOLD, SETTINGS)
    x = frame.select(FEATURES).to_numpy().astype(float)[:200]
    p = symmetric_predict(booster, x, record["rounds"])
    q = symmetric_predict(booster, swap(x), record["rounds"])
    np.testing.assert_allclose(p + q, 1.0, atol=1e-12)
    assert len(DIFF_FEATURES) < len(FEATURES)


def test_training_is_deterministic() -> None:
    frame = synthetic_frame()
    out1, rec1, _ = train_fold(frame, FOLD, SETTINGS)
    out2, rec2, _ = train_fold(frame, FOLD, SETTINGS)
    assert (
        out1.equals(out2) and rec1["chosen"] == rec2["chosen"] and rec1["rounds"] == rec2["rounds"]
    )


def test_test_year_labels_never_influence_anything() -> None:
    frame = synthetic_frame()
    out1, rec1, _ = train_fold(frame, FOLD, SETTINGS)
    flipped = frame.with_columns(
        pl.when(pl.col("tournament_start_date").dt.year() == 2014)
        .then(1 - pl.col("y"))
        .otherwise(pl.col("y"))
        .alias("y")
    )
    out2, rec2, _ = train_fold(flipped, FOLD, SETTINGS)
    assert np.array_equal(out1["p_gbm"].to_numpy(), out2["p_gbm"].to_numpy())
    assert np.array_equal(out1["p_gbm_recal"].to_numpy(), out2["p_gbm_recal"].to_numpy())
    assert rec1["candidates"] == rec2["candidates"]


def test_calibration_year_labels_only_change_the_slope() -> None:
    frame = synthetic_frame()
    out1, rec1, _ = train_fold(frame, FOLD, SETTINGS)
    flipped = frame.with_columns(
        pl.when(pl.col("tournament_start_date").dt.year() == 2013)
        .then(1 - pl.col("y"))
        .otherwise(pl.col("y"))
        .alias("y")
    )
    out2, rec2, _ = train_fold(flipped, FOLD, SETTINGS)
    assert rec1["candidates"] == rec2["candidates"]  # selection never sees the calibration year
    assert np.array_equal(out1["p_gbm"].to_numpy(), out2["p_gbm"].to_numpy())
    assert rec1["recalibration_slope"] != rec2["recalibration_slope"]


# --------------------------------------------------------------------------- inner-end reader


def test_inner_reader_never_returns_late_check_or_holdout_rows() -> None:
    splits = load_split_config(REPO_ROOT / "configs" / "splits.yaml")
    days = [date(2022, 6, 1), date(2022, 12, 31), date(2023, 1, 16), date(2024, 6, 24)]
    keys = [f"m{i}" for i in range(len(days))]
    tables = {
        "matches": pl.DataFrame({"match_key": keys, "tournament_start_date": days}),
        "tournaments": pl.DataFrame({"tournament_id": keys, "start_date": days}),
        "rankings": pl.DataFrame({"player_id": keys, "ranking_date": days}),
        "match_stats": pl.DataFrame({"match_key": keys, "player_id": keys}),
        "match_entry": pl.DataFrame({"match_key": keys, "player_id": keys}),
    }
    out = load_inner_tables(tables, splits)
    for name, frame in out.items():
        assert frame.height == 2, name
    assert out["matches"]["tournament_start_date"].max() == date(2022, 12, 31)


def scored_frame(gbm_shift: float) -> pl.DataFrame:
    rng = np.random.default_rng(3)
    n = 4000
    truth = rng.uniform(0.1, 0.9, size=n)
    y = (rng.random(n) < truth).astype(np.int8)
    noisy = np.clip(truth + rng.normal(0, 0.15, size=n), 0.02, 0.98)
    better = noisy if gbm_shift == 1.0 else np.clip(truth + gbm_shift * (noisy - truth), 0.02, 0.98)
    dates = [date(2013 + i % 3, 1, 1) + timedelta(days=7 * (i % 50)) for i in range(n)]
    return pl.DataFrame(
        {
            "match_key": [f"m{i}" for i in range(n)],
            "tournament_start_date": dates,
            "y": y,
            "p_gbm": better,
            "p_gbm_recal": better,
            "p_elo_overall_recal": noisy,
            "p_elo_surface_recal": noisy,
        }
    )


@pytest.mark.parametrize(("shift", "verdict"), [(0.0, "KEEP"), (1.0, "REJECT")])
def test_decision_rule_uses_the_upper_ci_bound(shift: float, verdict: str) -> None:
    config = Phase5Config(
        name="t", model_version="t", seed=1, bootstrap_replicates=200, baseline={},
        features={}, training={}, decision={"keep_if": "upper bound < 0"},
    )  # fmt: skip
    result = compare(scored_frame(shift), config)
    ci = result["decision"]["primary_ci"]
    assert result["decision"]["verdict"] == verdict
    assert (ci["ci_high"] < 0) == (verdict == "KEEP")
    assert set(result["by_year"]) == {"2013", "2014", "2015"}
