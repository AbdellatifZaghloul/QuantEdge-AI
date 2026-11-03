"""EXPLORATORY Phase 5 review: format-aware Elo, a simpler LightGBM and feature-audit sensitivity.

The inner folds were already inspected, so every result here is exploratory. ``phase5-gbm-v1``
is read from its registered run and never changed. Readers are cut at ``splits.inner_end``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl
import yaml

from quantedge.canonical.build import code_fingerprint, load_tables
from quantedge.core.manifest import BUILD_MANIFEST_NAME, read_build_manifest
from quantedge.core.splits import Fold, SplitConfig, walk_forward_folds
from quantedge.evaluation.bootstrap import block_bootstrap_means
from quantedge.evaluation.metrics import (
    apply_slope,
    brier_terms,
    fit_slope_recalibration,
    log_loss_terms,
    summary,
)
from quantedge.evaluation.walkforward import week_blocks
from quantedge.experiments.phase5 import (
    build_frame,
    gbm_settings,
    load_inner_tables,
    load_phase5_config,
)
from quantedge.experiments.registry import environment, git_state, make_run_id, register
from quantedge.models.format_elo import format_adjusted
from quantedge.models.gbm import FEATURES, train_folds

ELO_SCALE = 400.0


def elo_fold_predictions(
    frame: pl.DataFrame, folds: list[Fold], *, formatted: bool
) -> pl.DataFrame:
    """Reference Elo (optionally format-adjusted), slope-recalibrated on each fold's y-1."""
    p = 1.0 / (1.0 + np.power(10.0, -frame["elo_diff"].to_numpy() / ELO_SCALE))
    if formatted:
        p = format_adjusted(p, frame["best_of"].to_numpy())
    scored = frame.select("match_key", "tournament_start_date", "eval_eligible", "y").with_columns(
        pl.Series("p", p)
    )
    outs = []
    for fold in folds:

        def window(a: Any, b: Any) -> pl.DataFrame:
            return scored.filter(
                pl.col("eval_eligible") & pl.col("tournament_start_date").is_between(a, b)
            )

        cal, test = (
            window(fold.calibration_start, fold.calibration_end),
            window(fold.test_start, fold.test_end),
        )
        slope = fit_slope_recalibration(cal["y"].to_numpy().astype(float), cal["p"].to_numpy())
        outs.append(
            test.select("match_key", pl.Series("p_recal", apply_slope(test["p"].to_numpy(), slope)))
        )
    return pl.concat(outs)


def masked(frame: pl.DataFrame, drop: list[str]) -> pl.DataFrame:
    unknown = set(drop) - set(FEATURES)
    if unknown:
        raise ValueError(f"unknown features {sorted(unknown)}")
    return frame.with_columns(pl.lit(None, dtype=pl.Float64).alias(c) for c in drop)


def _subset(scored: pl.DataFrame, name: str) -> pl.DataFrame:
    if name == "all":
        return scored
    if name == "grand_slam_main":
        return scored.filter((pl.col("level") == "G") & (pl.col("draw_category") == "tour_main"))
    if name == "excluding_challenger_qualifying":
        return scored.filter(pl.col("draw_category") != "challenger_qualifying")
    raise ValueError(name)


def evaluate(
    scored: pl.DataFrame,
    models: list[str],
    contrasts: list[list[str]],
    subsets: list[str],
    *,
    replicates: int,
    seed: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in subsets:
        s = _subset(scored, name)
        y = s["y"].to_numpy().astype(float)
        terms: dict[str, np.ndarray] = {}
        for a, b in contrasts:
            pa, pb = s[f"p_{a}"].to_numpy(), s[f"p_{b}"].to_numpy()
            terms[f"log_loss::{a}-{b}"] = log_loss_terms(y, pa) - log_loss_terms(y, pb)
            terms[f"brier::{a}-{b}"] = brier_terms(y, pa) - brier_terms(y, pb)
        out[name] = {
            "n": s.height,
            "models": {m: summary(y, s[f"p_{m}"].to_numpy()) for m in models},
            "contrasts": block_bootstrap_means(
                week_blocks(s["tournament_start_date"]), terms, replicates=replicates, seed=seed
            ),
        }
    by_year: dict[str, dict[str, float]] = {}
    for (year,), g in scored.group_by(
        pl.col("tournament_start_date").dt.year(), maintain_order=True
    ):
        gy = g["y"].to_numpy().astype(float)
        by_year[str(year)] = {
            m: float(log_loss_terms(gy, g[f"p_{m}"].to_numpy()).mean()) for m in models
        }
    out["by_year_log_loss"] = by_year
    return out


def run_phase5_review(
    build_dir: Path, splits: SplitConfig, config_path: Path, *, runs_root: Path, repo_root: Path
) -> tuple[str, Path, dict[str, Any]]:
    review = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    v1 = load_phase5_config(repo_root / review["v1_config"])
    tables = load_inner_tables(load_tables(build_dir), splits)
    frame, _ = build_frame(tables, v1)
    folds = [f for f in walk_forward_folds(splits) if f.kind == "inner"]
    base = pl.read_parquet(runs_root / review["v1_run_id"] / "test_predictions.parquet").select(
        "match_key",
        "tournament_start_date",
        "y",
        pl.col("p_gbm_recal").alias("p_gbm_v1"),
        pl.col("p_elo_overall_recal").alias("p_elo_frozen"),
    )
    scored = base
    for name, spec in review["candidates"].items():
        if spec["kind"] in ("elo", "elo_format"):
            preds = elo_fold_predictions(frame, folds, formatted=spec["kind"] == "elo_format")
            preds = preds.rename({"p_recal": f"p_{name}"})
        else:
            test, _ = train_folds(masked(frame, list(spec["drop"])), folds, gbm_settings(v1))
            preds = test.select("match_key", pl.col("p_gbm_recal").alias(f"p_{name}"))
        scored = scored.join(preds, on="match_key", how="inner")
    if scored.height != base.height:
        raise RuntimeError("exploratory candidates do not cover the identical test population")
    meta = (
        tables["matches"]
        .select("match_key", "tournament_id", "draw_category", "best_of")
        .join(tables["tournaments"].select("tournament_id", "level"), on="tournament_id")
    )
    scored = scored.join(meta, on="match_key", how="left")
    models = ["elo_frozen", "gbm_v1", *review["candidates"].keys()]
    metrics = evaluate(
        scored,
        models,
        review["contrasts"],
        review["subsets"],
        replicates=int(review["bootstrap_replicates"]),
        seed=int(review["seed"]),
    )
    metrics["exploratory"] = True
    metrics["headline"] = {m: round(metrics["all"]["models"][m]["log_loss"], 5) for m in models}
    build_manifest = read_build_manifest(build_dir / BUILD_MANIFEST_NAME)
    payload = {
        "name": review["name"],
        "kind": "inner",
        "config": review,
        "build_id": build_manifest.build_id,
        "code_fingerprint": code_fingerprint(),
    }
    run_id = make_run_id(payload)
    run_dir = runs_root / run_id
    manifest = {
        "run_id": run_id,
        "name": review["name"],
        "kind": "inner",
        "exploratory": True,
        "model_version": review["model_version"],
        "git": git_state(repo_root),
        "code_fingerprint": payload["code_fingerprint"],
        "environment": environment() | {"lightgbm": lgb.__version__},
        "config": review,
        "config_text": config_path.read_text(encoding="utf-8"),
        "data": {
            "build_id": build_manifest.build_id,
            "view": "development",
            "read_end": str(splits.inner_end),
        },
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    scored.write_parquet(run_dir / "test_predictions.parquet")
    register(runs_root, run_dir, manifest, metrics)
    return run_id, run_dir, metrics
