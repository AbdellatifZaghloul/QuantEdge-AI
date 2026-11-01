"""Leakage-resistant pre-match rating features.

Every feature row for match *m* is read from state containing only results admissible for *m*
(knowability rule + earlier-round exception), computed by ``quantedge.core.asof.replay``.
Only eligibility-approved results update state; every match is a prediction target.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Literal

import polars as pl

from quantedge.core.asof import replay
from quantedge.ratings.elo import (
    EloConfig,
    EloState,
    apply_batch,
    read_features,
)

Ordering = Literal["conservative", "naive"]
UpdatePolicy = Literal["elo_update_baseline", "elo_update_retirements"]

_NAIVE_EPOCH = datetime(1900, 1, 1, tzinfo=UTC)


def prepare(matches: pl.DataFrame, eligibility: pl.DataFrame) -> pl.DataFrame:
    """Join eligibility; use effective (possibly deferred) knowability and precedence flags."""
    frame = matches.join(
        eligibility.select(
            "match_key",
            "surface",
            "elo_update_baseline",
            "elo_update_retirements",
            "eval_eligible",
            "effective_knowable_at_utc",
            "precedence_allowed",
            "primary_reason",
        ),
        on="match_key",
        how="inner",
    )
    if frame.height != matches.height:
        raise ValueError("every match needs exactly one eligibility row")
    return frame.with_columns(
        pl.col("effective_knowable_at_utc").alias("knowable_at_utc"),
        pl.col("precedence_allowed").alias("precedence_ok"),
    )


def naive_order(frame: pl.DataFrame) -> pl.DataFrame:
    """Sensitivity variant: trust the naive total order (start date, round, match_num).

    Every match sees all earlier rows in this order, including same-day results whose real
    availability is unknown. Used only to measure leakage exposure; never for official results.
    """
    ordered = frame.sort(
        "tournament_start_date", "round_ordinal", "source_match_num", "match_key", nulls_last=True
    ).with_row_index("_i")
    cutoff = pl.lit(_NAIVE_EPOCH) + pl.duration(microseconds=pl.col("_i").cast(pl.Int64) * 2)
    return ordered.with_columns(
        cutoff.cast(pl.Datetime("us", "UTC")).alias("prediction_cutoff_utc"),
        (cutoff + pl.duration(microseconds=1))
        .cast(pl.Datetime("us", "UTC"))
        .alias("knowable_at_utc"),
        pl.lit(False).alias("precedence_ok"),
    ).drop("_i")


def elo_features(
    prepared: pl.DataFrame,
    config: EloConfig,
    *,
    update_policy: UpdatePolicy = "elo_update_baseline",
    ordering: Ordering = "conservative",
) -> pl.DataFrame:
    frame = prepared
    if ordering == "naive":
        if config.decay_half_life_days is not None:
            raise ValueError("naive ordering has no real time scale; decay is not defined")
        frame = naive_order(frame)
    results = frame.filter(pl.col(update_policy))
    feats = replay(
        frame,
        results=results,
        init=lambda: EloState(config),
        apply_batch=apply_batch,
        copy_state=lambda s: s.fork(),
        read=read_features,
    )
    return feats.with_columns(
        _expected_expr(pl.col("elo_a"), pl.col("elo_b"), config.scale).alias("p_elo")
    ).sort("match_key")


def _expected_expr(r_a: pl.Expr, r_b: pl.Expr, scale: float) -> pl.Expr:
    return 1.0 / (1.0 + pl.lit(10.0).pow((r_b - r_a) / scale))


def surface_probability(
    feats: pl.DataFrame, pseudo_count: float, scale: float = 400.0
) -> pl.Series:
    """P(A wins) from partially pooled surface ratings (``m`` = pseudo-count, see ``elo.py``)."""
    label = "inf" if math.isinf(pseudo_count) else f"{pseudo_count:g}"
    pooled = {}
    for side in ("a", "b"):
        surface, n, overall = (
            pl.col(f"surface_elo_{side}"),
            pl.col(f"surface_n_{side}"),
            pl.col(f"elo_{side}"),
        )
        if math.isinf(pseudo_count):
            pooled[side] = overall
        elif pseudo_count == 0:
            pooled[side] = surface
        else:
            w = n / (n + pseudo_count)
            pooled[side] = w * surface + (1.0 - w) * overall
    return feats.select(
        _expected_expr(pooled["a"], pooled["b"], scale).alias(f"p_surface_m{label}")
    ).to_series()
