"""Point-in-time ATP ranking baseline.

Features for match *m* use, per player, the latest ranking with ``knowable_at_utc <= cutoff(m)``
(a ranking is knowable only after the end of its ranking date). Rankings older than
``staleness_days`` count as unranked.

Model (antisymmetric, no intercept because A/B orientation is random):
``P(A) = sigmoid(b1 * (log r_A - log r_B) + b2 * (u_A - u_B))`` where ``r`` is the rank capped at
``cap`` (unranked -> ``cap``) and ``u`` flags unranked players.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import polars as pl

from quantedge.evaluation.metrics import fit_logistic, sigmoid


def point_in_time_ranks(
    targets: pl.DataFrame, rankings: pl.DataFrame, *, staleness_days: int = 365
) -> pl.DataFrame:
    table = rankings.select("player_id", "knowable_at_utc", pl.col("rank").alias("_rank")).sort(
        "knowable_at_utc"
    )
    out = targets.select("match_key")
    for side in ("a", "b"):
        left = targets.select(
            "match_key", pl.col(f"player_{side}_id").alias("player_id"), "prediction_cutoff_utc"
        ).sort("prediction_cutoff_utc")
        joined = left.join_asof(
            table,
            left_on="prediction_cutoff_utc",
            right_on="knowable_at_utc",
            by="player_id",
            strategy="backward",
            check_sortedness=False,
        )
        stale = pl.col("prediction_cutoff_utc") - pl.col("knowable_at_utc") > timedelta(
            days=staleness_days
        )
        joined = joined.select(
            "match_key",
            pl.when(stale).then(None).otherwise(pl.col("_rank")).alias(f"rank_{side}"),
        )
        out = out.join(joined, on="match_key", how="left")
    return out


def design_matrix(frame: pl.DataFrame, *, cap: int = 2000) -> np.ndarray:
    cols = []
    logs = {}
    unranked = {}
    for side in ("a", "b"):
        rank = frame[f"rank_{side}"].to_numpy(allow_copy=True).astype(float)
        missing = np.isnan(rank)
        rank = np.where(missing, cap, np.minimum(rank, cap))
        logs[side] = np.log(rank)
        unranked[side] = missing.astype(float)
    cols.append(logs["a"] - logs["b"])
    cols.append(unranked["a"] - unranked["b"])
    return np.column_stack(cols)


class RankBaseline:
    def __init__(self, cap: int = 2000) -> None:
        self.cap = cap
        self.coef: np.ndarray | None = None

    def fit(self, frame: pl.DataFrame, y: np.ndarray) -> RankBaseline:
        self.coef = fit_logistic(design_matrix(frame, cap=self.cap), y, intercept=False)
        return self

    def predict(self, frame: pl.DataFrame) -> np.ndarray:
        if self.coef is None:
            raise RuntimeError("fit before predict")
        result: np.ndarray = sigmoid(design_matrix(frame, cap=self.cap) @ self.coef)
        return result
