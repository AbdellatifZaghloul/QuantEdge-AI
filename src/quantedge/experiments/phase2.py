"""Phase 2 experiment: Elo and surface Elo against a point-in-time ranking baseline.

Reads only through ``DataView(mode="development")``; the sealed 2025+ holdout is never loaded.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal

import numpy as np
import polars as pl
import yaml
from pydantic import BaseModel, ConfigDict

from quantedge.canonical.build import code_fingerprint, load_tables
from quantedge.core.dataview import DataView
from quantedge.core.hashing import frame_content_hash
from quantedge.core.manifest import BUILD_MANIFEST_NAME, read_build_manifest
from quantedge.core.splits import Fold, SplitConfig, walk_forward_folds
from quantedge.curation.eligibility import (
    POLICY_VERSION,
    build_eligibility,
    load_curation_reference,
)
from quantedge.evaluation.bootstrap import block_bootstrap_means
from quantedge.evaluation.metrics import (
    apply_slope,
    brier_terms,
    fit_slope_recalibration,
    log_loss_terms,
    summary,
)
from quantedge.evaluation.plots import fold_metric_plot, reliability_diagram
from quantedge.evaluation.walkforward import Family, evaluate_folds, week_blocks
from quantedge.experiments.registry import (
    environment,
    git_state,
    make_run_id,
    record_late_check_look,
    register,
)
from quantedge.models.rank_baseline import point_in_time_ranks
from quantedge.ratings.elo import EloConfig
from quantedge.ratings.features import elo_features, prepare, surface_probability

Kind = Literal["inner", "late_check"]
MODELS = (
    "coin",
    "rank",
    "rank_recal",
    "elo_overall",
    "elo_overall_recal",
    "elo_surface",
    "elo_surface_recal",
)
PAIRS = (
    ("elo_overall_recal", "rank_recal"),
    ("elo_surface_recal", "rank_recal"),
    ("elo_surface_recal", "elo_overall_recal"),
    ("elo_overall", "rank"),
)


class Sensitivity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reference: dict[str, Any]
    decay: tuple[dict[str, float], ...] = ()
    retirement_policies: tuple[str, ...] = ()
    orderings: tuple[str, ...] = ()


class Phase2Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    model_version: str
    seed: int
    bootstrap_replicates: int
    rank_staleness_days: int
    rank_cap: int
    elo_grid: tuple[dict[str, Any], ...]
    surface_pseudo_counts: tuple[float, ...]
    sensitivity: Sensitivity


def load_phase2_config(path: Path) -> Phase2Config:
    return Phase2Config.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _m_label(m: float) -> str:
    return "inf" if math.isinf(m) else f"{m:g}"


def build_predictions(
    tables: dict[str, pl.DataFrame], config: Phase2Config
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, list[str]]]:
    """Candidate probabilities for every development match plus point-in-time ranks."""
    matches, tournaments = tables["matches"], tables["tournaments"]
    eligibility = build_eligibility(matches, tournaments)
    prepared = prepare(matches, eligibility)
    frame = prepared.select(
        "match_key",
        "tournament_start_date",
        "eval_eligible",
        pl.col("a_won").cast(pl.Int8).alias("y"),
    ).join(
        point_in_time_ranks(
            prepared, tables["rankings"], staleness_days=config.rank_staleness_days
        ),
        on="match_key",
    )
    families: dict[str, list[str]] = {"elo_overall": [], "elo_surface": []}
    for params in config.elo_grid:
        elo_config = EloConfig.model_validate(params)
        feats = elo_features(prepared, elo_config)
        label = elo_config.label()
        columns = [feats["p_elo"].alias(f"elo::{label}")]
        families["elo_overall"].append(f"elo::{label}")
        for m in config.surface_pseudo_counts:
            name = f"surface::{label}::m{_m_label(m)}"
            columns.append(surface_probability(feats, m).alias(name))
            families["elo_surface"].append(name)
        frame = frame.join(pl.DataFrame([feats["match_key"], *columns]), on="match_key", how="left")
    return frame, eligibility, families


def sensitivity_columns(
    tables: dict[str, pl.DataFrame], eligibility: pl.DataFrame, config: Phase2Config
) -> pl.DataFrame:
    prepared = prepare(tables["matches"], eligibility)
    reference = config.sensitivity.reference
    out = prepared.select("match_key")
    variants: list[tuple[str, dict[str, Any], str, str]] = []
    for decay in config.sensitivity.decay:
        params = reference | decay
        label = EloConfig.model_validate(params).label().split("_", 1)[1]
        variants.append((f"decay_{label}", params, "elo_update_baseline", "conservative"))
    for policy in config.sensitivity.retirement_policies:
        variants.append((f"policy_{policy}", reference, policy, "conservative"))
    for ordering in config.sensitivity.orderings:
        variants.append((f"ordering_{ordering}", reference, "elo_update_baseline", ordering))
    for name, params, policy, ordering in variants:
        feats = elo_features(
            prepared,
            EloConfig.model_validate(params),
            update_policy=policy,  # type: ignore[arg-type]
            ordering=ordering,  # type: ignore[arg-type]
        )
        out = out.join(
            feats.select("match_key", pl.col("p_elo").alias(f"sens::{name}")), on="match_key"
        )
    return out


def fixed_column_metrics(
    predictions: pl.DataFrame, column: str, folds: list[Fold]
) -> dict[str, float]:
    """Pooled test metrics over folds for one fixed column (raw and per-fold slope-recalibrated)."""
    ys, raws, recals = [], [], []
    for fold in folds:

        def window(start: Any, end: Any) -> pl.DataFrame:
            return predictions.filter(
                pl.col("eval_eligible") & pl.col("tournament_start_date").is_between(start, end)
            )

        cal, test = (
            window(fold.calibration_start, fold.calibration_end),
            window(fold.test_start, fold.test_end),
        )
        slope = fit_slope_recalibration(cal["y"].to_numpy().astype(float), cal[column].to_numpy())
        ys.append(test["y"].to_numpy().astype(float))
        raws.append(test[column].to_numpy())
        recals.append(apply_slope(test[column].to_numpy(), slope))
    y, raw, recal = np.concatenate(ys), np.concatenate(raws), np.concatenate(recals)
    return {
        "n": float(len(y)),
        "log_loss_raw": float(log_loss_terms(y, raw).mean()),
        "log_loss_recal": float(log_loss_terms(y, recal).mean()),
        "brier_raw": float(brier_terms(y, raw).mean()),
        "brier_recal": float(brier_terms(y, recal).mean()),
    }


def _metrics(test: pl.DataFrame, config: Phase2Config) -> dict[str, Any]:
    y = test["y"].to_numpy().astype(float)
    per_model = {m: summary(y, test[f"p_{m}"].to_numpy()) for m in MODELS}
    per_fold = {
        fold: {m: summary(g["y"].to_numpy().astype(float), g[f"p_{m}"].to_numpy()) for m in MODELS}
        for (fold,), g in test.group_by(["fold"], maintain_order=True)
    }
    blocks = week_blocks(test["tournament_start_date"])
    terms: dict[str, np.ndarray] = {}
    for m in MODELS:
        terms[f"log_loss::{m}"] = log_loss_terms(y, test[f"p_{m}"].to_numpy())
        terms[f"brier::{m}"] = brier_terms(y, test[f"p_{m}"].to_numpy())
    for a, b in PAIRS:
        terms[f"log_loss_diff::{a}-{b}"] = terms[f"log_loss::{a}"] - terms[f"log_loss::{b}"]
        terms[f"brier_diff::{a}-{b}"] = terms[f"brier::{a}"] - terms[f"brier::{b}"]
    ci = block_bootstrap_means(
        blocks, terms, replicates=config.bootstrap_replicates, seed=config.seed
    )
    return {"pooled": per_model, "per_fold": per_fold, "bootstrap_95": ci}


def run_phase2(
    build_dir: Path,
    splits: SplitConfig,
    config: Phase2Config,
    config_text: str,
    *,
    runs_root: Path,
    repo_root: Path,
    kind: Kind = "inner",
    note: str = "",
) -> tuple[str, Path, dict[str, Any]]:
    view = DataView(load_tables(build_dir), splits, mode="development")
    tables = {name: view.table(name) for name in ("matches", "tournaments", "rankings")}
    folds = [f for f in walk_forward_folds(splits) if f.kind == kind]
    build_manifest = read_build_manifest(build_dir / BUILD_MANIFEST_NAME)

    predictions, eligibility, family_columns = build_predictions(tables, config)
    families = (
        Family("elo_overall", tuple(family_columns["elo_overall"])),
        Family("elo_surface", tuple(family_columns["elo_surface"])),
    )
    test, records = evaluate_folds(predictions, families, folds, rank_cap=config.rank_cap)

    payload = {
        "name": config.name,
        "kind": kind,
        "config": config.model_dump(mode="json"),
        "build_id": build_manifest.build_id,
        "code_fingerprint": code_fingerprint(),
        "eligibility_hash": frame_content_hash(eligibility),
    }
    run_id = make_run_id(payload)
    run_dir = runs_root / run_id
    look = None
    if kind == "late_check":
        look = record_late_check_look(
            runs_root, config.model_version, splits.late_check_max_looks, note or f"run {run_id}"
        )

    metrics = _metrics(test, config)
    metrics["folds"] = records
    if kind == "inner":
        sens = predictions.join(sensitivity_columns(tables, eligibility, config), on="match_key")
        reference_label = EloConfig.model_validate(config.sensitivity.reference).label()
        study: dict[str, Any] = {
            "note": "Descriptive only: computed on inner test folds and never used for selection.",
            "k_grid": {
                c: fixed_column_metrics(predictions, c, folds)
                for c in family_columns["elo_overall"]
            },
            "surface_pseudo_count": {
                c: fixed_column_metrics(predictions, c, folds)
                for c in family_columns["elo_surface"]
                if f"::{reference_label}::" in c
            },
            "variants_vs_reference": {
                "reference": fixed_column_metrics(predictions, f"elo::{reference_label}", folds),
                **{
                    c.removeprefix("sens::"): fixed_column_metrics(sens, c, folds)
                    for c in sens.columns
                    if c.startswith("sens::")
                },
            },
        }
        metrics["sensitivity"] = study
    pooled = metrics["pooled"]
    metrics["headline"] = {
        m: round(pooled[m]["log_loss"], 5)
        for m in ("rank_recal", "elo_overall_recal", "elo_surface_recal")
    }

    figures = run_dir / "figures"
    y = test["y"].to_numpy().astype(float)
    metrics["reliability"] = {
        "recalibrated": reliability_diagram(
            y,
            {
                m: test[f"p_{m}"].to_numpy()
                for m in ("rank_recal", "elo_overall_recal", "elo_surface_recal")
            },
            figures / "reliability_recalibrated.png",
            title=f"Reliability, pooled {kind} test folds (recalibrated)",
        ),
        "raw": reliability_diagram(
            y,
            {m: test[f"p_{m}"].to_numpy() for m in ("rank", "elo_overall", "elo_surface")},
            figures / "reliability_raw.png",
            title=f"Reliability, pooled {kind} test folds (raw)",
        ),
    }
    fold_names = [r["fold"] for r in records]
    fold_metric_plot(
        fold_names,
        {
            m: [metrics["per_fold"][f][m]["log_loss"] for f in fold_names]
            for m in ("rank_recal", "elo_overall_recal", "elo_surface_recal")
        },
        figures / "log_loss_by_fold.png",
        metric="log loss",
    )

    manifest = {
        "run_id": run_id,
        "name": config.name,
        "kind": kind,
        "late_check_look": look,
        "git": git_state(repo_root),
        "code_fingerprint": payload["code_fingerprint"],
        "environment": environment(),
        "seed": config.seed,
        "config": payload["config"],
        "config_text": config_text,
        "data": {
            "build_id": build_manifest.build_id,
            "raw_manifest_fingerprint": build_manifest.raw_manifest_fingerprint,
            "table_content_sha256": {t.name: t.content_sha256 for t in build_manifest.tables},
            "licence_lineage": [str(c) for c in build_manifest.licence_lineage],
            "view": "development",
            "development_end": str(splits.development_end),
        },
        "eligibility": {
            "policy_version": POLICY_VERSION,
            "curation_reference_fingerprint": load_curation_reference().fingerprint,
            "content_sha256": payload["eligibility_hash"],
        },
        "splits": {
            "config": splits.model_dump(mode="json"),
            "folds": records,
        },
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    test.write_parquet(run_dir / "test_predictions.parquet")
    register(runs_root, run_dir, manifest, metrics)
    (run_dir / "report.md").write_text(
        render_report(manifest, metrics), encoding="utf-8", newline="\n"
    )
    return run_id, run_dir, metrics


def _fmt(x: float) -> str:
    return f"{x:.4f}"


def render_report(manifest: dict[str, Any], metrics: dict[str, Any]) -> str:
    lines = [f"# Run {manifest['run_id']} — {manifest['name']} ({manifest['kind']})", ""]
    lines += [
        f"- Build: `{manifest['data']['build_id']}`; "
        f"eligibility `{manifest['eligibility']['policy_version']}`"
    ]
    lines += [f"- Git: `{manifest['git']}`; seed {manifest['seed']}", ""]
    lines += [
        "## Pooled test metrics",
        "",
        "| model | n | log loss | Brier | accuracy | ECE | cal. slope |",
        "|---|---|---|---|---|---|---|",
    ]
    for m, s in metrics["pooled"].items():
        lines.append(
            f"| {m} | {int(s['n'])} | {_fmt(s['log_loss'])} | {_fmt(s['brier'])} | "
            f"{_fmt(s['accuracy'])} | {_fmt(s['ece'])} | {_fmt(s['calibration_slope'])} |"
        )
    lines += [
        "",
        "## Block-bootstrap 95% intervals (tournament-week blocks)",
        "",
        "| quantity | estimate | 95% CI |",
        "|---|---|---|",
    ]
    for name, ci in metrics["bootstrap_95"].items():
        if "diff" in name or name.startswith("log_loss::"):
            lines.append(
                f"| {name} | {ci['estimate']:.5f} | [{ci['ci_low']:.5f}, {ci['ci_high']:.5f}] |"
            )
    lines += ["", "## Folds", ""]
    for r in metrics["folds"]:
        lines.append(
            f"- {r['fold']}: n_test={r['n_test']}, overall={r['elo_overall']['selected']}, "
            f"surface={r['elo_surface']['selected']}"
        )
    if "sensitivity" in metrics:
        lines += [
            "",
            "## Sensitivity (descriptive)",
            "",
            "```json",
            json.dumps(metrics["sensitivity"], indent=2),
            "```",
        ]
    return "\n".join(lines) + "\n"
