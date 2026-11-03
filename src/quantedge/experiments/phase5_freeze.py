"""Freeze record for ``phase5-gbm-v1`` (no retraining, no tuning, development data only).

``phase5-gbm-v1`` is a frozen *procedure*: the per-fold LightGBM models are re-fitted by the same
code, settings and rules on each fold's own fit window. Freezing pins that procedure:

- source files (and their dependencies) by SHA-256, verified against commit ``b55bfe4``;
- the experiment configuration, the original run and its artifacts by SHA-256;
- the categorical label-to-code mapping, pinned from development data (<= ``inner_end``) and
  verified to reproduce the original feature encoding exactly; labels never seen in development
  are encoded as missing (NaN), a rule fixed here before any late-check data exists.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import lightgbm as lgb
import polars as pl

from quantedge.core.splits import SplitConfig
from quantedge.experiments.phase5 import build_frame, load_inner_tables, load_phase5_config
from quantedge.models.gbm import FEATURES
from quantedge.models.gbm_features import CATEGORICAL, DIFF_FEATURES, SHARED_FEATURES

MODEL_VERSION = "phase5-gbm-v1"
SOURCE_COMMIT = "b55bfe4bffa03fde7213e27560a39d079b9155ad"
ORIGINAL_RUN_ID = "d10ff67cd3b7"
CONFIG_RELPATH = "configs/experiments/phase5_gbm.yaml"
FREEZE_RELPATH = "configs/frozen/phase5_gbm_v1.json"
# v1's own files plus every module whose behaviour it depends on.
FROZEN_FILES = (
    "src/quantedge/models/gbm.py",
    "src/quantedge/models/gbm_features.py",
    "src/quantedge/experiments/phase5.py",
    "src/quantedge/ratings/features.py",
    "src/quantedge/ratings/elo.py",
    "src/quantedge/core/asof.py",
    "src/quantedge/core/dataview.py",
    "src/quantedge/core/splits.py",
    "src/quantedge/models/rank_baseline.py",
    "src/quantedge/curation/eligibility.py",
    "src/quantedge/evaluation/metrics.py",
    "src/quantedge/evaluation/bootstrap.py",
    "src/quantedge/evaluation/walkforward.py",
)
RUN_ARTIFACTS = ("test_predictions.parquet", "metrics.json", "manifest.json")
# Raw label source per categorical feature (code column -> label column).
CATEGORY_SOURCES = {"level_code": "level", "surface_code": "surface", "draw_code": "draw_category"}
UNSEEN_LABEL_POLICY = "encode_as_missing_nan"


class FreezeError(RuntimeError):
    """The frozen v1 procedure cannot be reproduced or has changed."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def committed_sha256(repo_root: Path, commit: str, rel: str) -> str:
    blob = subprocess.run(
        ["git", "show", f"{commit}:{rel}"], cwd=repo_root, capture_output=True, check=True
    ).stdout
    return hashlib.sha256(blob.replace(b"\r\n", b"\n")).hexdigest()


def category_labels(tables: dict[str, pl.DataFrame], eligibility: pl.DataFrame) -> pl.DataFrame:
    """Raw labels behind v1's categorical codes (identity metadata, no outcome)."""
    return (
        tables["matches"]
        .select("match_key", "tournament_id", "draw_category")
        .join(
            tables["tournaments"].select("tournament_id", "level"), on="tournament_id", how="left"
        )
        .join(eligibility.select("match_key", "surface"), on="match_key", how="left")
        .select("match_key", "level", "surface", "draw_category")
    )


def pinned_mapping(labels: pl.DataFrame) -> dict[str, dict[str, int]]:
    """v1's rule (sorted non-null labels -> 0..k-1), evaluated once on development data."""
    out = {}
    for code, column in CATEGORY_SOURCES.items():
        values = sorted(v for v in labels[column].unique().to_list() if v is not None)
        out[code] = {v: i for i, v in enumerate(values)}
    return out


def encode(labels: pl.DataFrame, mapping: dict[str, dict[str, int]]) -> pl.DataFrame:
    """Apply the pinned mapping; labels absent from it become NaN (missing)."""
    exprs = [
        pl.col(column)
        .replace_strict(mapping[code], default=None, return_dtype=pl.Float64)
        .alias(code)
        for code, column in CATEGORY_SOURCES.items()
    ]
    return labels.select("match_key", *exprs)


def unseen_label_counts(labels: pl.DataFrame, mapping: dict[str, dict[str, int]]) -> dict[str, int]:
    return {
        code: int(
            labels.filter(
                pl.col(column).is_not_null() & ~pl.col(column).is_in(list(mapping[code]))
            ).height
        )
        for code, column in CATEGORY_SOURCES.items()
    }


def build_freeze_record(
    tables_all: dict[str, pl.DataFrame], splits: SplitConfig, *, repo_root: Path, runs_root: Path
) -> dict[str, Any]:
    files = {}
    for rel in FROZEN_FILES:
        now, committed = (
            sha256_file(repo_root / rel),
            committed_sha256(repo_root, SOURCE_COMMIT, rel),
        )
        if now != committed:
            raise FreezeError(f"{rel} differs from commit {SOURCE_COMMIT[:7]}")
        files[rel] = now
    config_path = repo_root / CONFIG_RELPATH
    if sha256_file(config_path) != committed_sha256(repo_root, SOURCE_COMMIT, CONFIG_RELPATH):
        raise FreezeError("experiment configuration differs from the source commit")
    config = load_phase5_config(config_path)
    run_dir = runs_root / ORIGINAL_RUN_ID
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    tables = load_inner_tables(tables_all, splits)
    frame, eligibility = build_frame(tables, config)
    labels = category_labels(tables, eligibility)
    mapping = pinned_mapping(labels)
    pinned = encode(labels, mapping)
    joined = frame.select("match_key", *CATEGORY_SOURCES).join(
        pinned, on="match_key", suffix="_pin"
    )
    identical = all(
        bool(
            (joined[c].fill_null(-1.0) == joined[f"{c}_pin"].fill_null(-1.0)).all()
            and joined[c].null_count() == joined[f"{c}_pin"].null_count()
        )
        for c in CATEGORY_SOURCES
    )
    if joined.height != frame.height or not identical:
        raise FreezeError("pinned mapping does not reproduce v1's development encoding")
    subset = labels.join(
        tables["matches"].select("match_key", "tournament_start_date"), on="match_key"
    ).filter(pl.col("tournament_start_date").dt.year() >= 2013)
    training = config.training
    return {
        "model_version": MODEL_VERSION,
        "status": "FROZEN",
        "nature": "frozen training procedure; per-fold models are re-fitted on each fold's window",
        "source_commit": SOURCE_COMMIT,
        "original_run": {
            "run_id": ORIGINAL_RUN_ID,
            "artifacts_sha256": {a: sha256_file(run_dir / a) for a in RUN_ARTIFACTS},
            "package_code_fingerprint": manifest["code_fingerprint"],
            "git_at_run": manifest["git"],
            "note": (
                "The run was executed from an uncommitted tree on top of 469d393 (dirty=true). "
                "The package fingerprint of commit b55bfe4 recomputed from git equals the run's "
                "fingerprint, so the run's code is byte-identical to b55bfe4."
            ),
        },
        "files_sha256": files,
        "config": {"path": CONFIG_RELPATH, "sha256": sha256_file(config_path)},
        "environment": manifest["environment"] | {"lightgbm_now": lgb.__version__},
        "training": {
            "grid": training["grid"],
            "fixed": training["fixed"],
            "max_rounds": training["max_rounds"],
            "early_stopping_rounds": training["early_stopping_rounds"],
            "seed": config.seed,
            "selection": "lowest symmetrized validation log-loss on the fit window's last year "
            "(train on years <= y-3); ties -> lowest grid index; refit on the full fit window "
            "with the selected rounds",
            "calibration": "slope-only logistic recalibration on year y-1",
            "symmetrize": "p = (f(x) + 1 - f(swap(x))) / 2",
            "lightgbm_params": "objective binary, deterministic, force_col_wise, num_threads 4, "
            "all seeds = config seed",
        },
        "population": {
            "rows": "eval_eligible (Gate-0 eligibility.v1)",
            "folds": "walk_forward_folds(splits): fit dev_start..y-2, calibration y-1, test y",
        },
        "features": {
            "order": list(FEATURES),
            "antisymmetric": list(DIFF_FEATURES),
            "shared": list(SHARED_FEATURES),
            "categorical": list(CATEGORICAL),
            "category_mapping": mapping,
            "category_sources": CATEGORY_SOURCES,
            "unseen_label_policy": UNSEEN_LABEL_POLICY,
            "mapping_source": f"development data <= {splits.inner_end}",
            "mapping_reproduces_v1_encoding": {
                "rows_compared": joined.height,
                "identical": identical,
            },
            "label_sets_equal_on_2013_2022_subset": pinned_mapping(subset) == mapping,
            "missing_values": "LightGBM native NaN handling; ranks capped at 2000 with indicator; "
            "players without knowable statistics shrunk to the knowable tour mean",
            "point_in_time": "Elo/ranks via Phase 2 replay (knowability rule plus earlier-round "
            "outcome exception); statistics, form and workload via a knowability-only sweep",
            "known_limitations": [
                "rounds_into_draw = round_ordinal - min(round_ordinal) over the draw's complete "
                "records, not over pre-tournament structure. It equals the structural first round "
                "in all but ~28 of ~3,900 ordinary draws (other differences are Davis Cup / Finals "
                "round-robin labelling) and never depends on results; development effect +0.0003.",
                "category codes were data-derived in v1; the late check uses this pinned mapping.",
            ],
        },
    }


def write_freeze_record(record: dict[str, Any], repo_root: Path) -> Path:
    path = repo_root / FREEZE_RELPATH
    if path.exists():
        raise FreezeError(f"{path} already exists (write-once)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return path


def load_freeze_record(repo_root: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((repo_root / FREEZE_RELPATH).read_text(encoding="utf-8"))
    return data


def verify_freeze_record(record: dict[str, Any], repo_root: Path) -> None:
    """Fail closed unless every frozen file, the config and the environment still match."""
    if record.get("model_version") != MODEL_VERSION or record.get("status") != "FROZEN":
        raise FreezeError("freeze record is not the frozen phase5-gbm-v1")
    for rel, digest in record["files_sha256"].items():
        if sha256_file(repo_root / rel) != digest:
            raise FreezeError(f"frozen file changed: {rel}")
    if sha256_file(repo_root / record["config"]["path"]) != record["config"]["sha256"]:
        raise FreezeError("frozen experiment configuration changed")
    if record["features"]["order"] != list(FEATURES):
        raise FreezeError("feature order changed")
    if lgb.__version__ != record["environment"]["lightgbm"]:
        raise FreezeError(
            f"LightGBM {lgb.__version__} != frozen {record['environment']['lightgbm']}"
        )
