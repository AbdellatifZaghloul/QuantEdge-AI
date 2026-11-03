"""Phase 5 LightGBM candidate: nested chronological training on the Phase 2 inner folds.

Per fold (test year *y*):

1. **Selection** inside the fit window only: train on years ``<= y-3``, early-stop and score each
   grid configuration on year ``y-2`` (the last fit-window year).
2. **Refit** the chosen configuration on the whole fit window with the selected number of rounds.
3. **Calibration:** slope-only recalibration on year ``y-1`` (same as Phase 2; antisymmetric).
4. **Test** on year *y*.

Predictions are symmetrized, ``p = (f(x) + 1 - f(swap(x))) / 2``, so ``p(A, B) = 1 - p(B, A)``
exactly. Nothing from the calibration or test year influences selection or fitting.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl

from quantedge.core.splits import Fold
from quantedge.evaluation.metrics import apply_slope, fit_slope_recalibration, log_loss_terms
from quantedge.models.gbm_features import CATEGORICAL, DIFF_FEATURES, SHARED_FEATURES, swap

FEATURES = (*DIFF_FEATURES, *SHARED_FEATURES)
EPS = 1e-6


@dataclass(frozen=True)
class GbmSettings:
    grid: tuple[dict[str, Any], ...]
    fixed: dict[str, Any]
    max_rounds: int = 2000
    early_stopping_rounds: int = 50
    seed: int = 0
    num_threads: int = 4


def _params(config: dict[str, Any], settings: GbmSettings) -> dict[str, Any]:
    return {
        "objective": "binary",
        "metric": "binary_logloss",
        "verbosity": -1,
        "deterministic": True,
        "force_col_wise": True,
        "num_threads": settings.num_threads,
        "seed": settings.seed,
        "bagging_seed": settings.seed,
        "feature_fraction_seed": settings.seed,
        "data_random_seed": settings.seed,
        **settings.fixed,
        **config,
    }


def matrix(frame: pl.DataFrame) -> np.ndarray:
    out: np.ndarray = frame.select(FEATURES).to_numpy().astype(float)
    return out


def symmetric_predict(booster: lgb.Booster, x: np.ndarray, rounds: int) -> np.ndarray:
    f = np.asarray(booster.predict(x, num_iteration=rounds), dtype=float)
    g = np.asarray(booster.predict(swap(x), num_iteration=rounds), dtype=float)
    p = 0.5 * (f + 1.0 - g)
    out: np.ndarray = np.clip(p, EPS, 1.0 - EPS)
    return out


def _dataset(x: np.ndarray, y: np.ndarray, reference: lgb.Dataset | None = None) -> lgb.Dataset:
    cats = [FEATURES.index(c) for c in CATEGORICAL]
    return lgb.Dataset(
        x, label=y, categorical_feature=cats, reference=reference, free_raw_data=False,
        feature_name=list(FEATURES),
    )  # fmt: skip


def _train(
    config: dict[str, Any],
    settings: GbmSettings,
    x: np.ndarray,
    y: np.ndarray,
    rounds: int,
    valid: tuple[np.ndarray, np.ndarray] | None = None,
) -> lgb.Booster:
    train = _dataset(x, y)
    callbacks: list[Callable[..., Any]] = []
    valid_sets: list[lgb.Dataset] = []
    if valid is not None:
        valid_sets = [_dataset(valid[0], valid[1], reference=train)]
        callbacks = [lgb.early_stopping(settings.early_stopping_rounds, verbose=False)]
    return lgb.train(
        _params(config, settings), train, num_boost_round=rounds, valid_sets=valid_sets,
        callbacks=callbacks,
    )  # fmt: skip


def train_fold(
    frame: pl.DataFrame, fold: Fold, settings: GbmSettings
) -> tuple[pl.DataFrame, dict[str, Any], lgb.Booster]:
    """``frame``: match_key, tournament_start_date, eval_eligible, y and FEATURES."""

    def window(start: Any, end: Any) -> pl.DataFrame:
        return frame.filter(
            pl.col("eval_eligible") & pl.col("tournament_start_date").is_between(start, end)
        )

    fit = window(fold.fit_start, fold.fit_end)
    val_year = fold.fit_end.year
    inner = fit.filter(pl.col("tournament_start_date").dt.year() < val_year)
    val = fit.filter(pl.col("tournament_start_date").dt.year() == val_year)
    cal = window(fold.calibration_start, fold.calibration_end)
    test = window(fold.test_start, fold.test_end)
    if min(inner.height, val.height, cal.height, test.height) == 0:
        raise ValueError(f"{fold.name}: empty training, validation, calibration or test window")
    xi, yi = matrix(inner), inner["y"].to_numpy().astype(float)
    xv, yv = matrix(val), val["y"].to_numpy().astype(float)
    candidates: list[dict[str, Any]] = []
    for k, config in enumerate(settings.grid):
        booster = _train(config, settings, xi, yi, settings.max_rounds, valid=(xv, yv))
        rounds = max(int(booster.best_iteration or settings.max_rounds), 1)
        loss = float(log_loss_terms(yv, symmetric_predict(booster, xv, rounds)).mean())
        candidates.append({"index": k, "config": config, "rounds": rounds, "val_log_loss": loss})
    chosen = min(candidates, key=lambda c: (c["val_log_loss"], c["index"]))
    final = _train(
        chosen["config"], settings, matrix(fit), fit["y"].to_numpy().astype(float), chosen["rounds"]
    )
    cal_raw = symmetric_predict(final, matrix(cal), chosen["rounds"])
    slope = fit_slope_recalibration(cal["y"].to_numpy().astype(float), cal_raw)
    raw = symmetric_predict(final, matrix(test), chosen["rounds"])
    out = test.select("match_key", "tournament_start_date", "y").with_columns(
        pl.lit(fold.name).alias("fold"),
        pl.Series("p_gbm", raw),
        pl.Series("p_gbm_recal", apply_slope(raw, slope)),
    )
    gain = final.feature_importance(importance_type="gain")
    record = {
        "fold": fold.name,
        "n_inner_train": inner.height,
        "n_validation": val.height,
        "n_fit": fit.height,
        "n_calibration": cal.height,
        "n_test": test.height,
        "validation_year": val_year,
        "candidates": candidates,
        "chosen": chosen["index"],
        "rounds": chosen["rounds"],
        "recalibration_slope": slope,
        "gain": {f: float(g) for f, g in zip(FEATURES, gain, strict=True)},
    }
    return out, record, final


def train_folds(
    frame: pl.DataFrame, folds: Sequence[Fold], settings: GbmSettings
) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    outs, records = [], []
    for fold in folds:
        out, record, _ = train_fold(frame, fold, settings)
        outs.append(out)
        records.append(record)
    return pl.concat(outs), records
