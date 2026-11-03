"""Phase 5 experiment: LightGBM on point-in-time features vs the frozen Phase 2 Elo baseline.

Development data only, and **every reader is cut at ``splits.inner_end``** (2022-12-31): the
2023-24 late check and the 2025+ holdout are never loaded. Evaluation is on exactly the frozen
Phase 2 inner test matches (run ``0a5b056019ec``), so both models are scored on identical rows.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl
import yaml
from pydantic import BaseModel, ConfigDict

from quantedge.canonical.build import code_fingerprint, load_tables
from quantedge.core.dataview import DataView
from quantedge.core.manifest import BUILD_MANIFEST_NAME, read_build_manifest
from quantedge.core.splits import SplitConfig, walk_forward_folds
from quantedge.curation.eligibility import build_eligibility
from quantedge.evaluation.bootstrap import block_bootstrap_means
from quantedge.evaluation.metrics import (
    brier_terms,
    log_loss_terms,
    reliability_table,
    summary,
)
from quantedge.evaluation.walkforward import week_blocks
from quantedge.experiments.registry import environment, git_state, make_run_id, register
from quantedge.models.gbm import FEATURES, GbmSettings, train_folds
from quantedge.models.gbm_features import FeatureParams, build_features
from quantedge.ratings.features import prepare

INNER_TABLES = ("matches", "tournaments", "rankings", "match_stats", "match_entry")
MODELS = ("gbm", "gbm_recal", "elo_overall_recal", "elo_surface_recal")
PAIRS = (("gbm_recal", "elo_overall_recal"), ("gbm_recal", "elo_surface_recal"))


class Phase5Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    model_version: str
    seed: int
    bootstrap_replicates: int
    baseline: dict[str, Any]
    features: dict[str, Any]
    training: dict[str, Any]
    decision: dict[str, Any]


def load_phase5_config(path: Path) -> Phase5Config:
    return Phase5Config.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_inner_tables(
    tables: dict[str, pl.DataFrame], splits: SplitConfig
) -> dict[str, pl.DataFrame]:
    """Development view cut at the end of the inner folds; refuses anything later."""
    view = DataView(tables, splits, mode="development")
    out = {name: view.table(name, end=splits.inner_end) for name in INNER_TABLES}
    latest = out["matches"]["tournament_start_date"].max()
    if latest is not None and latest > splits.inner_end:  # type: ignore[operator]
        raise RuntimeError("inner-fold reader returned rows after inner_end")
    return out


def feature_params(config: Phase5Config) -> FeatureParams:
    f = config.features
    return FeatureParams(
        elo_reference=dict(f["elo_reference"]),
        rank_staleness_days=int(f["rank_staleness_days"]),
        rank_cap=int(f["rank_cap"]),
        stats_half_life_days=float(f["stats_half_life_days"]),
        stats_prior_points=float(f["stats_prior_points"]),
        surface_stats_prior_points=float(f["surface_stats_prior_points"]),
        form_half_life_days=float(f["form_half_life_days"]),
        workload_windows_days=tuple(int(w) for w in f["workload_windows_days"]),  # type: ignore[arg-type]
        inactivity_days=float(f["inactivity_days"]),
    )


def gbm_settings(config: Phase5Config) -> GbmSettings:
    t = config.training
    return GbmSettings(
        grid=tuple(dict(g) for g in t["grid"]),
        fixed=dict(t["fixed"]),
        max_rounds=int(t["max_rounds"]),
        early_stopping_rounds=int(t["early_stopping_rounds"]),
        seed=config.seed,
    )


def build_frame(
    tables: dict[str, pl.DataFrame], config: Phase5Config
) -> tuple[pl.DataFrame, pl.DataFrame]:
    eligibility = build_eligibility(tables["matches"], tables["tournaments"])
    prepared = prepare(tables["matches"], eligibility)
    features = build_features(
        prepared,
        tournaments=tables["tournaments"],
        rankings=tables["rankings"],
        match_stats=tables["match_stats"],
        match_entry=tables["match_entry"],
        params=feature_params(config),
    )
    frame = prepared.select(
        "match_key",
        "tournament_start_date",
        "eval_eligible",
        pl.col("a_won").cast(pl.Int8).alias("y"),
    ).join(features, on="match_key", how="left")
    return frame, eligibility


def missingness(frame: pl.DataFrame, start: date, end: date) -> dict[str, float]:
    rows = frame.filter(
        pl.col("eval_eligible") & pl.col("tournament_start_date").is_between(start, end)
    )
    n = max(rows.height, 1)
    return {c: float(rows[c].is_null().sum()) / n for c in FEATURES} | {"n": float(rows.height)}


def compare(scored: pl.DataFrame, config: Phase5Config) -> dict[str, Any]:
    y = scored["y"].to_numpy().astype(float)
    pooled = {m: summary(y, scored[f"p_{m}"].to_numpy()) for m in MODELS}
    by_year = {}
    for (year,), g in scored.group_by(
        pl.col("tournament_start_date").dt.year(), maintain_order=True
    ):
        gy = g["y"].to_numpy().astype(float)
        by_year[str(year)] = {m: summary(gy, g[f"p_{m}"].to_numpy()) for m in MODELS}
    terms: dict[str, np.ndarray] = {}
    for m in MODELS:
        terms[f"log_loss::{m}"] = log_loss_terms(y, scored[f"p_{m}"].to_numpy())
        terms[f"brier::{m}"] = brier_terms(y, scored[f"p_{m}"].to_numpy())
    for a, b in PAIRS:
        terms[f"log_loss_diff::{a}-{b}"] = terms[f"log_loss::{a}"] - terms[f"log_loss::{b}"]
        terms[f"brier_diff::{a}-{b}"] = terms[f"brier::{a}"] - terms[f"brier::{b}"]
    ci = block_bootstrap_means(
        week_blocks(scored["tournament_start_date"]),
        terms,
        replicates=config.bootstrap_replicates,
        seed=config.seed,
    )
    year_diffs = {}
    for (year,), g in scored.group_by(
        pl.col("tournament_start_date").dt.year(), maintain_order=True
    ):
        gy = g["y"].to_numpy().astype(float)
        d = log_loss_terms(gy, g["p_gbm_recal"].to_numpy()) - log_loss_terms(
            gy, g["p_elo_overall_recal"].to_numpy()
        )
        year_diffs[str(year)] = float(d.mean())
    primary = ci["log_loss_diff::gbm_recal-elo_overall_recal"]
    return {
        "pooled": pooled,
        "by_year": by_year,
        "bootstrap_95": ci,
        "yearly_log_loss_diff_gbm_minus_elo": year_diffs,
        "reliability": {m: reliability_table(y, scored[f"p_{m}"].to_numpy()) for m in MODELS},
        "decision": {
            "rule": config.decision["keep_if"],
            "primary_ci": primary,
            "verdict": "KEEP" if primary["ci_high"] < 0 else "REJECT",
        },
    }


def run_phase5(
    build_dir: Path,
    splits: SplitConfig,
    config: Phase5Config,
    config_text: str,
    *,
    runs_root: Path,
    repo_root: Path,
) -> tuple[str, Path, dict[str, Any]]:
    tables = load_inner_tables(load_tables(build_dir), splits)
    build_manifest = read_build_manifest(build_dir / BUILD_MANIFEST_NAME)
    frame, _ = build_frame(tables, config)
    folds = [f for f in walk_forward_folds(splits) if f.kind == "inner"]
    test, records = train_folds(frame, folds, gbm_settings(config))

    baseline_id = config.baseline["phase2_run_id"]
    base = pl.read_parquet(runs_root / baseline_id / "test_predictions.parquet").select(
        "match_key", pl.col("y").alias("y_base"), "p_elo_overall_recal", "p_elo_surface_recal"
    )
    scored = test.join(base, on="match_key", how="inner")
    if scored.height != test.height or scored.height != base.height:
        raise RuntimeError("LightGBM and Elo test populations differ")
    if not (scored["y"].cast(pl.Int8) == scored["y_base"].cast(pl.Int8)).all():
        raise RuntimeError("label mismatch with the frozen Elo run")

    payload = {
        "name": config.name,
        "kind": "inner",
        "config": config.model_dump(mode="json"),
        "build_id": build_manifest.build_id,
        "code_fingerprint": code_fingerprint(),
    }
    run_id = make_run_id(payload)
    run_dir = runs_root / run_id
    first_test = min(f.test_start for f in folds)
    metrics = compare(scored, config)
    metrics["folds"] = records
    metrics["missingness"] = {
        "development_2005_2022": missingness(frame, splits.development_start, splits.inner_end),
        "inner_test_2013_2022": missingness(frame, first_test, splits.inner_end),
    }
    metrics["population"] = {
        "test_matches": scored.height,
        "features": list(FEATURES),
        "n_features": len(FEATURES),
    }
    metrics["headline"] = {m: round(metrics["pooled"][m]["log_loss"], 5) for m in MODELS}
    manifest = {
        "run_id": run_id,
        "name": config.name,
        "kind": "inner",
        "model_version": config.model_version,
        "git": git_state(repo_root),
        "code_fingerprint": payload["code_fingerprint"],
        "environment": environment() | {"lightgbm": lgb.__version__},
        "seed": config.seed,
        "config": payload["config"],
        "config_text": config_text,
        "baseline_run_id": baseline_id,
        "data": {
            "build_id": build_manifest.build_id,
            "table_content_sha256": {t.name: t.content_sha256 for t in build_manifest.tables},
            "licence_lineage": [str(c) for c in build_manifest.licence_lineage],
            "view": "development",
            "read_end": str(splits.inner_end),
        },
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    scored.drop("y_base").write_parquet(run_dir / "test_predictions.parquet")
    register(runs_root, run_dir, manifest, metrics)
    (run_dir / "summary.json").write_text(
        json.dumps({k: metrics[k] for k in ("headline", "decision", "population")}, indent=2,
                   default=str) + "\n",
        encoding="utf-8",
    )  # fmt: skip
    return run_id, run_dir, metrics
