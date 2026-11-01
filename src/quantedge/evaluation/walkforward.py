"""Nested chronological evaluation inside the development period (VALIDATION.md §2-4).

For each fold with test year *y*:

1. **Selection:** among candidate probability columns of one model family, choose the one with the
   lowest mean log loss on eligible matches in ``[development_start, Dec 31 of y-2]``.
2. **Calibration:** fit a slope-only recalibration on year ``y-1``.
3. **Test:** score year *y*, raw and recalibrated.

The ranking baseline is fitted on the same selection window and recalibrated on the same year, so
every model is scored on the identical eligible population with the identical protocol. Nothing
from the test year influences any choice for that fold.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from quantedge.core.splits import Fold
from quantedge.evaluation.metrics import apply_slope, fit_slope_recalibration, log_loss_terms
from quantedge.models.rank_baseline import RankBaseline


@dataclass(frozen=True)
class Family:
    """A model family: candidate probability columns from which one is selected per fold."""

    name: str
    candidates: tuple[str, ...]


def _mask(frame: pl.DataFrame, start: date, end: date) -> pl.Series:
    return (
        frame["eval_eligible"]
        & (frame["tournament_start_date"] >= start)
        & (frame["tournament_start_date"] <= end)
    )


def evaluate_folds(
    predictions: pl.DataFrame,
    families: tuple[Family, ...],
    folds: list[Fold],
    *,
    rank_cap: int = 2000,
) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    """Return per-row test predictions (all folds) and per-fold selection records.

    ``predictions`` needs ``match_key``, ``tournament_start_date``, ``eval_eligible``, ``y``,
    ``rank_a``, ``rank_b`` and every candidate column.
    """
    test_frames = []
    records: list[dict[str, Any]] = []
    for fold in folds:
        fit = predictions.filter(_mask(predictions, fold.fit_start, fold.fit_end))
        cal = predictions.filter(_mask(predictions, fold.calibration_start, fold.calibration_end))
        test = predictions.filter(_mask(predictions, fold.test_start, fold.test_end))
        if min(fit.height, cal.height, test.height) == 0:
            raise ValueError(f"{fold.name}: empty fit, calibration or test window")
        y_fit = fit["y"].to_numpy().astype(float)
        y_cal = cal["y"].to_numpy().astype(float)
        out = test.select("match_key", "tournament_start_date", "y").with_columns(
            pl.lit(fold.name).alias("fold"), pl.lit(0.5).alias("p_coin")
        )
        record: dict[str, Any] = {
            "fold": fold.name,
            "kind": fold.kind,
            "fit": [str(fold.fit_start), str(fold.fit_end)],
            "calibration": [str(fold.calibration_start), str(fold.calibration_end)],
            "test": [str(fold.test_start), str(fold.test_end)],
            "n_fit": fit.height,
            "n_calibration": cal.height,
            "n_test": test.height,
        }
        for family in families:
            losses = {
                c: float(log_loss_terms(y_fit, fit[c].to_numpy()).mean()) for c in family.candidates
            }
            chosen = min(sorted(losses), key=lambda c: losses[c])
            slope = fit_slope_recalibration(y_cal, cal[chosen].to_numpy())
            raw = test[chosen].to_numpy()
            out = out.with_columns(
                pl.Series(f"p_{family.name}", raw),
                pl.Series(f"p_{family.name}_recal", apply_slope(raw, slope)),
            )
            record[family.name] = {
                "selected": chosen,
                "fit_log_loss": losses[chosen],
                "recalibration_slope": slope,
            }
        baseline = RankBaseline(cap=rank_cap).fit(fit, y_fit)
        cal_raw = baseline.predict(cal)
        slope = fit_slope_recalibration(y_cal, cal_raw)
        raw = baseline.predict(test)
        out = out.with_columns(
            pl.Series("p_rank", raw), pl.Series("p_rank_recal", apply_slope(raw, slope))
        )
        assert baseline.coef is not None
        record["rank"] = {"coef": [float(c) for c in baseline.coef], "recalibration_slope": slope}
        test_frames.append(out)
        records.append(record)
    return pl.concat(test_frames), records


def week_blocks(dates: pl.Series) -> np.ndarray:
    """Tournament-week block labels (ISO year-week of the tournament start date)."""
    frame = dates.to_frame("d").select(
        pl.col("d").dt.iso_year().cast(pl.String) + "-" + pl.col("d").dt.week().cast(pl.String)
    )
    out: np.ndarray = frame.to_series().to_numpy()
    return out
