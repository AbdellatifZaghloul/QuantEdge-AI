"""Late-check runner for the frozen ``phase5-gbm-v1`` (prepared; execution needs owner approval).

Order of operations for an execution (each step fails closed):

1. explicit owner approval flags (``approve`` and the exact model version), else refuse;
2. verify the frozen confirmation rule, the freeze record and every frozen file by SHA-256;
3. verify the look trail is consistent (registry, audit log and durable backup agree) and that no
   confirmatory look has completed;
4. **record the look** in the registry *and* both append-only audit trails (fsynced);
5. only now call the data loader; any failure from here on is recorded and still counts;
6. train each late-check fold with the frozen procedure (pinned category mapping), align with the
   registered Phase 2 Elo late-check predictions (missing, extra or duplicate keys and label
   mismatches abort), compute the pre-registered primary result and secondaries.

``dry_run`` performs steps 2-3 and reports the plan; it never loads late-check data.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from quantedge.core.lazy_tables import load_development_tables
from quantedge.core.splits import Fold, SplitConfig, walk_forward_folds
from quantedge.evaluation.bootstrap import block_bootstrap_means
from quantedge.evaluation.metrics import brier_terms, log_loss_terms, summary
from quantedge.evaluation.walkforward import week_blocks
from quantedge.experiments.phase5 import (
    INNER_TABLES,
    build_frame,
    gbm_settings,
    load_phase5_config,
)
from quantedge.experiments.phase5_freeze import (
    CATEGORY_SOURCES,
    CONFIG_RELPATH,
    MODEL_VERSION,
    category_labels,
    encode,
    load_freeze_record,
    sha256_file,
    unseen_label_counts,
    verify_freeze_record,
)
from quantedge.experiments.registry import LOOKS, record_late_check_look
from quantedge.models.gbm import GbmSettings, train_fold

RULE_RELPATH = "configs/preregistration/phase5_gbm_v1_late_check.yaml"
RULE_SHA256 = "9a76d4e8ec51b167a2d273e9f946f2131885ca343e49a1ef39ed4f0aef1c5da3"
FREEZE_SHA256 = "5887fae1431e6d2b63134e05dfddd38d2b301bc21bed0cee8bba0e6ee8e2e1c5"
AUDIT_NAME = "late_check_audit.jsonl"


class LateCheckRefusedError(PermissionError):
    """The late check may not run (approval, integrity or look-limit failure)."""


class AlignmentError(RuntimeError):
    """LightGBM and Elo late-check populations or labels do not align exactly."""


@dataclass(frozen=True)
class LateCheckInputs:
    frame: pl.DataFrame  # match_key, tournament_start_date, eval_eligible, y, FEATURES
    labels: pl.DataFrame  # match_key, level, surface, draw_category
    elo: pl.DataFrame  # registered Phase 2 late-check predictions


# ------------------------------------------------------------------------ verification


def verify_prepared(repo_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    rule_path = repo_root / RULE_RELPATH
    if sha256_file(rule_path) != RULE_SHA256:
        raise LateCheckRefusedError("confirmation rule changed after it was frozen")
    rule: dict[str, Any] = yaml.safe_load(rule_path.read_text(encoding="utf-8"))
    freeze_path = repo_root / rule["freeze_record"]["path"]
    digest = sha256_file(freeze_path)
    if digest != rule["freeze_record"]["sha256"] or digest != FREEZE_SHA256:
        raise LateCheckRefusedError("freeze record changed after it was frozen")
    freeze = load_freeze_record(repo_root)
    try:
        verify_freeze_record(freeze, repo_root)
    except Exception as exc:
        raise LateCheckRefusedError(str(exc)) from exc
    return rule, freeze


def late_check_folds(splits: SplitConfig, rule: dict[str, Any]) -> list[Fold]:
    folds = [f for f in walk_forward_folds(splits) if f.kind == "late_check"]
    expected = [
        (int(f["test_year"]), str(f["fit_end"]), int(f["calibration_year"])) for f in rule["folds"]
    ]
    actual = [(f.test_start.year, str(f.fit_end), f.calibration_start.year) for f in folds]
    if actual != expected:
        raise LateCheckRefusedError(f"late-check folds {actual} differ from the rule {expected}")
    return folds


# -------------------------------------------------------------------------- look trail


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _append(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def look_state(runs_root: Path, backup_path: Path) -> dict[str, Any]:
    """Registry looks, audit and backup must agree; any divergence refuses execution."""
    registry_path = runs_root / LOOKS
    looks = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else {}
    registry = len(looks.get(MODEL_VERSION, []))
    audit, backup = _read_jsonl(runs_root / AUDIT_NAME), _read_jsonl(backup_path)
    if audit != backup:
        raise LateCheckRefusedError("late-check audit log and its backup differ")
    opened = [e for e in audit if e["event"] == "opened"]
    if len(opened) != registry:
        raise LateCheckRefusedError(
            f"registry records {registry} looks but the audit trail records {len(opened)}"
        )
    completed = [e for e in audit if e["event"] == "completed"]
    return {"looks_used": registry, "completed": len(completed), "events": audit}


def _event(kind: str, look: int, **extra: Any) -> dict[str, Any]:
    stamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"event": kind, "look": look, "at_utc": stamp, "model_version": MODEL_VERSION} | extra


# ---------------------------------------------------------------------------- analysis


def _align(gbm: pl.DataFrame, elo: pl.DataFrame) -> pl.DataFrame:
    for name, frame in (("lightgbm", gbm), ("elo", elo)):
        if frame["match_key"].n_unique() != frame.height:
            raise AlignmentError(f"{name}: duplicate match keys")
        if frame.select("match_key", "y").null_count().sum_horizontal().item() != 0:
            raise AlignmentError(f"{name}: null keys or labels")
    base = elo.select("match_key", pl.col("y").alias("y_elo"), "p_elo_overall_recal")
    joined = gbm.join(base, on="match_key", how="full", coalesce=True)
    if (
        joined.height != gbm.height
        or joined.height != elo.height
        or joined.null_count().sum_horizontal().item()
    ):
        raise AlignmentError("LightGBM and Elo late-check populations differ")
    if not (joined["y"].cast(pl.Int8) == joined["y_elo"].cast(pl.Int8)).all():
        raise AlignmentError("label mismatch with the registered Elo late-check predictions")
    return joined.drop("y_elo")


def _pinned(inputs: LateCheckInputs, freeze: dict[str, Any]) -> tuple[pl.DataFrame, dict[str, int]]:
    mapping = freeze["features"]["category_mapping"]
    codes = encode(inputs.labels, mapping)
    frame = inputs.frame.drop(list(CATEGORY_SOURCES)).join(codes, on="match_key", how="left")
    return frame, unseen_label_counts(inputs.labels, mapping)


def _predict(frame: pl.DataFrame, folds: list[Fold], settings: GbmSettings) -> pl.DataFrame:
    return pl.concat([train_fold(frame, fold, settings)[0] for fold in folds])


def _ci(scored: pl.DataFrame, a: str, b: str, rule: dict[str, Any]) -> dict[str, dict[str, float]]:
    y = scored["y"].to_numpy().astype(float)
    terms = {
        "log_loss": log_loss_terms(y, scored[a].to_numpy())
        - log_loss_terms(y, scored[b].to_numpy()),
        "brier": brier_terms(y, scored[a].to_numpy()) - brier_terms(y, scored[b].to_numpy()),
    }
    u = rule["uncertainty"]
    return block_bootstrap_means(
        week_blocks(scored["tournament_start_date"]), terms,
        replicates=int(u["replicates"]), seed=int(u["seed"]), level=float(u["level"]),
    )  # fmt: skip


def _subgroup(scored: pl.DataFrame, rule: dict[str, Any]) -> dict[str, Any]:
    if scored.height == 0:
        return {"n": 0}
    out: dict[str, Any] = {"n": scored.height}
    return out | _ci(scored, "p_gbm_recal", "p_elo_overall_recal", rule)


def analyse(
    inputs: LateCheckInputs,
    *,
    rule: dict[str, Any],
    freeze: dict[str, Any],
    folds: list[Fold],
    settings: GbmSettings,
) -> tuple[dict[str, Any], pl.DataFrame]:
    frame, unseen = _pinned(inputs, freeze)
    gbm = _predict(frame, folds, settings)
    scored = _align(gbm, inputs.elo)
    sens = _predict(
        frame.with_columns(pl.lit(None, dtype=pl.Float64).alias("rounds_into_draw")),
        folds,
        settings,
    )
    scored = scored.join(
        sens.select("match_key", pl.col("p_gbm_recal").alias("p_gbm_no_rounds")), on="match_key"
    ).join(inputs.labels.select("match_key", "level", "draw_category"), on="match_key", how="left")
    y = scored["y"].to_numpy().astype(float)
    models = {"gbm_v1": "p_gbm_recal", "elo_frozen": "p_elo_overall_recal"}
    primary = _ci(scored, "p_gbm_recal", "p_elo_overall_recal", rule)
    slam = scored.filter((pl.col("level") == "G") & (pl.col("draw_category") == "tour_main"))
    result = {
        "protocol_id": rule["protocol_id"],
        "rule_sha256": RULE_SHA256,
        "freeze_sha256": FREEZE_SHA256,
        "n_matches": scored.height,
        "unseen_category_labels": unseen,
        "primary": {
            "metric": rule["primary"]["metric"],
            "log_loss_diff": primary["log_loss"],
            "decision": "CONFIRMED" if primary["log_loss"]["ci_high"] < 0 else "NOT_CONFIRMED",
        },
        "secondary": {
            "brier_diff": primary["brier"],
            "models": {m: summary(y, scored[c].to_numpy()) for m, c in models.items()},
            "per_year": {
                str(year): _subgroup(group, rule)
                for (year,), group in scored.group_by(
                    pl.col("tournament_start_date").dt.year(), maintain_order=True
                )
            },
            "grand_slam_main": _subgroup(slam, rule),
            "sensitivity_without_rounds_into_draw": _ci(
                scored, "p_gbm_no_rounds", "p_elo_overall_recal", rule
            ),
        },
        "disclosure": rule["disclosure"],
    }
    return result, scored


# ------------------------------------------------------------------------ entry points


def dry_run(
    repo_root: Path, splits: SplitConfig, *, runs_root: Path, backup_path: Path
) -> dict[str, Any]:
    """Verify everything and describe the plan; never loads late-check data."""
    rule, freeze = verify_prepared(repo_root)
    state = look_state(runs_root, backup_path)
    baseline = runs_root / rule["baseline"]["phase2_late_check_run_id"] / rule["baseline"]["file"]
    size = baseline.stat().st_size if baseline.exists() else None  # metadata only, not contents
    return {
        "would_read": {
            "view": "development",
            "end": str(splits.development_end),
            "loader": "lazy date-filtered parquet scan (no rows after end materialized)",
        },
        "folds": [
            {
                "test": f.test_start.year,
                "fit_end": str(f.fit_end),
                "calibration": f.calibration_start.year,
            }
            for f in late_check_folds(splits, rule)
        ],
        "looks_used": state["looks_used"],
        "confirmatory_looks_completed": state["completed"],
        "baseline_file_present": size is not None,
        "baseline_size_matches_rule": size == rule["baseline"]["size_bytes"],
        "freeze_record_verified": freeze["model_version"],
        "late_check_data_loaded": False,
    }


def execute(
    *,
    repo_root: Path,
    splits: SplitConfig,
    runs_root: Path,
    backup_path: Path,
    approve: bool,
    confirm_model_version: str,
    note: str,
    loader: Callable[[], LateCheckInputs],
    settings: GbmSettings | None = None,
) -> dict[str, Any]:
    if not approve or confirm_model_version != MODEL_VERSION:
        raise LateCheckRefusedError("explicit owner approval for phase5-gbm-v1 is required")
    if not note.strip():
        raise LateCheckRefusedError("a note describing the approval is required")
    rule, freeze = verify_prepared(repo_root)
    folds = late_check_folds(splits, rule)
    frozen = settings or gbm_settings(load_phase5_config(repo_root / freeze["config"]["path"]))
    state = look_state(runs_root, backup_path)
    if state["completed"] >= int(rule["looks"]["confirmatory_looks"]):
        raise LateCheckRefusedError("the confirmatory late-check look has already completed")
    look = record_late_check_look(runs_root, MODEL_VERSION, splits.late_check_max_looks, note)
    opened = _event("opened", look, note=note, rule_sha256=RULE_SHA256)
    for path in (runs_root / AUDIT_NAME, backup_path):
        _append(path, opened)
    try:
        inputs = loader()
        result, scored = analyse(inputs, rule=rule, freeze=freeze, folds=folds, settings=frozen)
    except BaseException as exc:
        failed = _event("failed", look, error=f"{type(exc).__name__}: {exc}"[:500])
        for path in (runs_root / AUDIT_NAME, backup_path):
            _append(path, failed)
        raise
    out_dir = runs_root / f"late_check_{MODEL_VERSION}_look{look}"
    out_dir.mkdir(parents=True, exist_ok=False)
    scored.write_parquet(out_dir / "late_check_predictions.parquet")
    (out_dir / "result.json").write_text(
        json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
    )
    digest = hashlib.sha256((out_dir / "result.json").read_bytes()).hexdigest()
    done = _event("completed", look, decision=result["primary"]["decision"], result_sha256=digest)
    for path in (runs_root / AUDIT_NAME, backup_path):
        _append(path, done)
    return result | {"look": look, "out_dir": str(out_dir)}


def make_loader(
    build_dir: Path, splits: SplitConfig, *, repo_root: Path, runs_root: Path, rule: dict[str, Any]
) -> Callable[[], LateCheckInputs]:
    """The only 2023-2024 reader; ``execute`` calls it strictly after the look is recorded."""

    def load() -> LateCheckInputs:
        tables = load_development_tables(
            build_dir, splits, INNER_TABLES, end=splits.development_end
        )
        latest = tables["matches"]["tournament_start_date"].max()
        if latest is not None and latest > splits.development_end:  # type: ignore[operator]
            raise RuntimeError("late-check reader returned rows after development_end")
        config = load_phase5_config(repo_root / CONFIG_RELPATH)
        frame, eligibility = build_frame(tables, config)
        base = rule["baseline"]
        elo = pl.read_parquet(runs_root / base["phase2_late_check_run_id"] / base["file"]).select(
            "match_key", "y", base["column"]
        )
        return LateCheckInputs(frame=frame, labels=category_labels(tables, eligibility), elo=elo)

    return load
