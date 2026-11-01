"""Market probability baselines and model-vs-market comparison on identical populations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from quantedge.evaluation.bootstrap import block_bootstrap_means
from quantedge.evaluation.metrics import brier_terms, log_loss_terms, summary
from quantedge.evaluation.walkforward import week_blocks
from quantedge.odds.devig import METHODS, DevigError, Method, devig


class HoldoutLeakError(PermissionError):
    """Raised when a development comparison would touch sealed-holdout matches."""


def market_probabilities(
    selected: pl.DataFrame, links: pl.DataFrame, methods: Sequence[Method] = METHODS
) -> pl.DataFrame:
    """Per (decision, bookmaker): raw implied and de-vigged P(player A wins).

    Requires a linked event and a selection name equal to one of the event participants; rows that
    fail any check carry ``usable = False`` and a reason, never a silent default.
    """
    frame = selected.filter(pl.col("status") == "available").join(
        links.filter(pl.col("status") == "linked").select(
            "event_id", "match_key", "participant_1_side"
        ),
        on="event_id",
    )
    rows = []
    for r in frame.iter_rows(named=True):
        out: dict[str, Any] = {
            k: r[k]
            for k in (
                "event_id",
                "decision_time_utc",
                "bookmaker_key",
                "match_key",
                "observed_at_utc",
            )
        }
        odds = (r["odds_1"], r["odds_2"])
        out["booksum"] = None
        try:
            implied = [1.0 / o for o in odds]
            out["booksum"] = sum(implied)
            results = {m: devig(odds, m).probabilities for m in methods}
        except DevigError as exc:
            rows.append(out | {"usable": False, "reason": str(exc)})
            continue
        # selection_1 belongs to participant_1 of the event?  The selector keeps provider order;
        # map by name against the event's side assignment computed at link time.
        a_is_first = r["participant_1_side"] == "A"
        idx_a = 0 if (r["selection_1_is_participant_1"] == a_is_first) else 1
        out["p_raw_a"] = implied[idx_a]
        for m, probs in results.items():
            out[f"p_{m}_a"] = probs[idx_a]
        rows.append(out | {"usable": True, "reason": ""})
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def attach_participant_order(selected: pl.DataFrame, events: pl.DataFrame) -> pl.DataFrame:
    """Flag whether ``selection_1`` is the event's ``participant_1`` (names must match exactly)."""
    joined = selected.join(
        events.select("event_id", "participant_1", "participant_2"), on="event_id", how="left"
    )
    valid = (
        (pl.col("selection_1") == pl.col("participant_1"))
        & (pl.col("selection_2") == pl.col("participant_2"))
    ) | (
        (pl.col("selection_1") == pl.col("participant_2"))
        & (pl.col("selection_2") == pl.col("participant_1"))
    )
    return joined.with_columns(
        (pl.col("selection_1") == pl.col("participant_1")).alias("selection_1_is_participant_1"),
        pl.when((pl.col("status") == "available") & ~valid.fill_null(False))
        .then(pl.lit("selection_names_mismatch"))
        .otherwise(pl.col("status"))
        .alias("status"),
    ).drop("participant_1", "participant_2")


def consensus(probabilities: pl.DataFrame, column: str) -> pl.DataFrame:
    """Median across bookmakers per decision (a probability baseline, not an executable price)."""
    return (
        probabilities.filter(pl.col("usable"))
        .group_by("match_key", "decision_time_utc")
        .agg(pl.col(column).median().alias(column), pl.len().alias("n_bookmakers"))
    )


def compare_on_identical_population(
    frame: pl.DataFrame,
    columns: Sequence[str],
    *,
    holdout_start: date,
    replicates: int = 2000,
    seed: int = 0,
) -> dict[str, Any]:
    """Score several probability columns on exactly the rows where all are present.

    ``frame`` needs ``y``, ``tournament_start_date`` and the columns. Refuses holdout rows.
    """
    if frame.filter(pl.col("tournament_start_date") >= holdout_start).height:
        raise HoldoutLeakError("comparison frame contains sealed-holdout matches")
    common = frame.drop_nulls(subset=list(columns))
    y = common["y"].to_numpy().astype(float)
    result: dict[str, Any] = {
        "n_input": frame.height,
        "n_common": common.height,
        "dropped_missing_any": frame.height - common.height,
        "metrics": {c: summary(y, common[c].to_numpy()) for c in columns} if common.height else {},
    }
    if common.height and len(columns) > 1:
        terms: dict[str, np.ndarray] = {}
        base = columns[0]
        for c in columns:
            terms[f"log_loss::{c}"] = log_loss_terms(y, common[c].to_numpy())
            terms[f"brier::{c}"] = brier_terms(y, common[c].to_numpy())
        for c in columns[1:]:
            terms[f"log_loss_diff::{c}-{base}"] = (
                terms[f"log_loss::{c}"] - terms[f"log_loss::{base}"]
            )
        result["bootstrap_95"] = block_bootstrap_means(
            week_blocks(common["tournament_start_date"]), terms, replicates=replicates, seed=seed
        )
    return result
