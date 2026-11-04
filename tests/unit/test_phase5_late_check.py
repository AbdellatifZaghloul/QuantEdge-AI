"""phase5-gbm-v1 freeze and late-check runner: synthetic data only (no 2023-2024 data is read)."""

from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from quantedge.core.splits import SplitConfig
from quantedge.experiments import phase5_late_check as lc
from quantedge.experiments.phase5_freeze import (
    CONFIG_RELPATH,
    FREEZE_RELPATH,
    FROZEN_FILES,
    FreezeError,
    encode,
    load_freeze_record,
    pinned_mapping,
    sha256_file,
    unseen_label_counts,
    verify_freeze_record,
)
from quantedge.experiments.registry import LOOKS, LateCheckLimitError
from quantedge.models.gbm import FEATURES, GbmSettings

REPO = Path(__file__).resolve().parents[2]
SPLITS = SplitConfig(
    burn_in_end=date(2016, 12, 31),
    development_start=date(2017, 1, 1),
    development_end=date(2024, 12, 31),
    holdout_start=date(2025, 1, 1),
    inner_test_years=(),
    late_check_years=(2023, 2024),
    late_check_max_looks=3,
)
SETTINGS = GbmSettings(
    grid=({"num_leaves": 7, "min_data_in_leaf": 20, "learning_rate": 0.1},),
    fixed={"feature_fraction": 0.9},
    max_rounds=60, early_stopping_rounds=10, seed=3, num_threads=2,
)  # fmt: skip
PINNED = {
    "level_code": {"A": 0, "C": 1, "D": 2, "F": 3, "G": 4, "M": 5},
    "surface_code": {"Carpet": 0, "Clay": 1, "Grass": 2, "Hard": 3},
    "draw_code": {
        "challenger_main": 0, "challenger_qualifying": 1, "tour_main": 2, "tour_qualifying": 3,
    },
}  # fmt: skip


def _sigmoid(x: np.ndarray) -> np.ndarray:
    out: np.ndarray = 1 / (1 + np.exp(-x))
    return out


def synthetic(per_year: int = 300, *, elo_signal: float = 0.2, seed: int = 0) -> lc.LateCheckInputs:
    rng = np.random.default_rng(seed)
    years = np.repeat(np.arange(2017, 2025), per_year)
    n = len(years)
    keys = [f"m{i}" for i in range(n)]
    offsets = rng.integers(0, 350, n)
    dates = [
        date(int(y), 1, 2) + timedelta(days=int(d)) for y, d in zip(years, offsets, strict=True)
    ]
    features = {f: rng.normal(size=n) for f in FEATURES}
    p_true = _sigmoid(1.2 * features["elo_diff"] + 0.6 * features["log_rank_diff"])
    y = (rng.random(n) < p_true).astype(np.int8)
    labels = pl.DataFrame(
        {
            "match_key": keys,
            "level": rng.choice(["A", "G", "M"], n),
            "surface": rng.choice(["Hard", "Clay", "Grass"], n),
            "draw_category": rng.choice(["tour_main", "challenger_main"], n),
        }
    )
    frame = pl.DataFrame(
        {"match_key": keys, "tournament_start_date": dates, "eval_eligible": [True] * n, "y": y}
        | {f: v for f, v in features.items()}
    ).with_columns(pl.lit(99.0).alias(c) for c in ("level_code", "surface_code", "draw_code"))
    test = years >= 2023
    elo = pl.DataFrame(
        {
            "match_key": np.array(keys)[test],
            "y": y[test],
            "p_elo_overall_recal": _sigmoid(elo_signal * features["elo_diff"][test])
            if elo_signal
            else p_true[test],
        }
    )
    return lc.LateCheckInputs(frame=frame, labels=labels, elo=elo)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for rel in (lc.RULE_RELPATH, FREEZE_RELPATH, CONFIG_RELPATH, *FROZEN_FILES):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, root / rel)
    return root


def _run(repo: Path, tmp_path: Path, loader: Any, **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "repo_root": repo,
        "splits": SPLITS,
        "runs_root": tmp_path / "runs",
        "backup_path": tmp_path / "backup" / "audit.jsonl",
        "approve": True,
        "confirm_model_version": "phase5-gbm-v1",
        "note": "synthetic test",
        "loader": loader,
        "settings": SETTINGS,
    } | kw
    return lc.execute(**args)


def _looks(tmp_path: Path) -> int:
    path = tmp_path / "runs" / LOOKS
    return len(json.loads(path.read_text())["phase5-gbm-v1"]) if path.exists() else 0


def _never(*_: Any, **__: Any) -> Any:
    raise AssertionError("late-check data must not be loaded")


# ---------------------------------------------------------------- frozen artefacts


def test_real_rule_and_freeze_record_are_pinned() -> None:
    assert sha256_file(REPO / lc.RULE_RELPATH) == lc.RULE_SHA256
    assert sha256_file(REPO / FREEZE_RELPATH) == lc.FREEZE_SHA256
    rule, freeze = lc.verify_prepared(REPO)
    assert rule["freeze_record"]["sha256"] == lc.FREEZE_SHA256
    assert rule["decision"]["confirmed_if"] == "ci_high < 0"
    assert rule["uncertainty"] == {
        "method": "iso_week_block_bootstrap", "replicates": 2000, "seed": 20261103, "level": 0.95,
    }  # fmt: skip
    assert freeze["features"]["order"] == list(FEATURES)
    assert freeze["features"]["category_mapping"] == PINNED
    assert freeze["features"]["mapping_reproduces_v1_encoding"]["identical"] is True


@pytest.mark.parametrize(
    "rel", [lc.RULE_RELPATH, FREEZE_RELPATH, CONFIG_RELPATH, "src/quantedge/models/gbm.py"]
)
def test_any_tampered_frozen_file_refuses(repo: Path, tmp_path: Path, rel: str) -> None:
    lc.verify_prepared(repo)
    with (repo / rel).open("a", encoding="utf-8") as handle:
        handle.write("\n# tampered\n")
    with pytest.raises(lc.LateCheckRefusedError):
        lc.verify_prepared(repo)
    with pytest.raises(lc.LateCheckRefusedError):
        _run(repo, tmp_path, _never)
    assert _looks(tmp_path) == 0


def test_feature_order_and_lightgbm_version_are_verified(repo: Path) -> None:
    record = load_freeze_record(repo)
    verify_freeze_record(record, repo)
    swapped = json.loads(json.dumps(record))
    swapped["features"]["order"] = list(reversed(record["features"]["order"]))
    with pytest.raises(FreezeError, match="feature order"):
        verify_freeze_record(swapped, repo)
    other = json.loads(json.dumps(record))
    other["environment"]["lightgbm"] = "0.0.0"
    with pytest.raises(FreezeError, match="LightGBM"):
        verify_freeze_record(other, repo)


def test_pinned_mapping_and_unseen_labels() -> None:
    labels = pl.DataFrame(
        {
            "match_key": ["a", "b", "c"],
            "level": ["G", "X", None],
            "surface": ["Clay", "Hard", "Ice"],
            "draw_category": ["tour_main", "tour_main", "tour_main"],
        }
    )
    out = encode(labels, PINNED)
    assert out["level_code"].to_list() == [4.0, None, None]
    assert out["surface_code"].to_list() == [1.0, 3.0, None]
    counts = unseen_label_counts(labels, PINNED)
    assert counts == {"level_code": 1, "surface_code": 1, "draw_code": 0}
    assert pinned_mapping(labels.filter(pl.col("match_key") == "a"))["level_code"] == {"G": 0}


def test_data_derived_codes_are_replaced_by_the_pinned_mapping(repo: Path) -> None:
    inputs = synthetic(60)
    frame, unseen = lc._pinned(inputs, load_freeze_record(repo))
    assert unseen == {"level_code": 0, "surface_code": 0, "draw_code": 0}
    assert set(frame["level_code"].unique().to_list()) <= {0.0, 4.0, 5.0}
    assert frame.height == inputs.frame.height and set(FEATURES) <= set(frame.columns)


# ---------------------------------------------------------------- access control


def test_dry_run_loads_no_data_and_records_no_look(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lc, "load_development_tables", _never)
    monkeypatch.setattr(pl, "read_parquet", _never)
    monkeypatch.setattr(lc, "record_late_check_look", _never)
    plan = lc.dry_run(repo, SPLITS, runs_root=tmp_path / "runs", backup_path=tmp_path / "b.jsonl")
    assert plan["late_check_data_loaded"] is False and plan["looks_used"] == 0
    assert [f["test"] for f in plan["folds"]] == [2023, 2024]
    assert not (tmp_path / "runs").exists() and not (tmp_path / "b.jsonl").exists()


@pytest.mark.parametrize(
    "kw",
    [
        {"approve": False},
        {"confirm_model_version": "phase5-gbm-v2"},
        {"confirm_model_version": ""},
        {"note": "  "},
    ],
)
def test_unauthorized_execution_is_refused_before_any_access(
    repo: Path, tmp_path: Path, kw: dict[str, Any]
) -> None:
    with pytest.raises(lc.LateCheckRefusedError):
        _run(repo, tmp_path, _never, **kw)
    assert _looks(tmp_path) == 0 and not (tmp_path / "runs" / lc.AUDIT_NAME).exists()


def test_look_is_recorded_in_registry_audit_and_backup_before_loading(
    repo: Path, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    def loader() -> lc.LateCheckInputs:
        seen["looks"] = _looks(tmp_path)
        seen["audit"] = lc._read_jsonl(tmp_path / "runs" / lc.AUDIT_NAME)
        seen["backup"] = lc._read_jsonl(tmp_path / "backup" / "audit.jsonl")
        return synthetic()

    result = _run(repo, tmp_path, loader)
    assert seen["looks"] == 1
    assert [e["event"] for e in seen["audit"]] == ["opened"] == [e["event"] for e in seen["backup"]]
    assert result["look"] == 1
    events = lc._read_jsonl(tmp_path / "runs" / lc.AUDIT_NAME)
    assert [e["event"] for e in events] == ["opened", "completed"]
    assert events == lc._read_jsonl(tmp_path / "backup" / "audit.jsonl")


def test_failure_after_opening_counts_and_completion_blocks_repeats(
    repo: Path, tmp_path: Path
) -> None:
    def broken() -> lc.LateCheckInputs:
        raise OSError("disk went away")

    with pytest.raises(OSError):
        _run(repo, tmp_path, broken)
    assert _looks(tmp_path) == 1
    events = lc._read_jsonl(tmp_path / "runs" / lc.AUDIT_NAME)
    assert [e["event"] for e in events] == ["opened", "failed"]
    assert _run(repo, tmp_path, synthetic)["look"] == 2
    with pytest.raises(lc.LateCheckRefusedError, match="already completed"):
        _run(repo, tmp_path, _never)
    assert _looks(tmp_path) == 2


def test_registry_cap_cannot_be_bypassed_by_failures(repo: Path, tmp_path: Path) -> None:
    def broken() -> lc.LateCheckInputs:
        raise RuntimeError("boom")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            _run(repo, tmp_path, broken)
    with pytest.raises(LateCheckLimitError):
        _run(repo, tmp_path, _never)
    assert _looks(tmp_path) == 3


@pytest.mark.parametrize("victim", ["audit", "backup", "registry"])
def test_tampered_or_deleted_trail_refuses(repo: Path, tmp_path: Path, victim: str) -> None:
    def broken() -> lc.LateCheckInputs:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _run(repo, tmp_path, broken)
    path = {
        "audit": tmp_path / "runs" / lc.AUDIT_NAME,
        "backup": tmp_path / "backup" / "audit.jsonl",
        "registry": tmp_path / "runs" / LOOKS,
    }[victim]
    path.unlink()
    with pytest.raises(lc.LateCheckRefusedError):
        _run(repo, tmp_path, _never)


def test_frozen_files_are_not_modified_by_execution(repo: Path, tmp_path: Path) -> None:
    rels = (lc.RULE_RELPATH, FREEZE_RELPATH, CONFIG_RELPATH, *FROZEN_FILES)
    before = {rel: sha256_file(repo / rel) for rel in rels}
    result = _run(repo, tmp_path, synthetic)
    assert {rel: sha256_file(repo / rel) for rel in rels} == before
    assert Path(result["out_dir"]).parent == tmp_path / "runs"


# ---------------------------------------------------------------- analysis


def test_test_year_labels_never_change_that_folds_predictions(repo: Path) -> None:
    inputs = synthetic()
    frame, _ = lc._pinned(inputs, load_freeze_record(repo))
    rule, _ = lc.verify_prepared(repo)
    folds = lc.late_check_folds(SPLITS, rule)
    a = lc._predict(frame, folds, SETTINGS)
    for year in (2023, 2024):
        flipped = frame.with_columns(
            pl.when(pl.col("tournament_start_date").dt.year() == year)
            .then(1 - pl.col("y"))
            .otherwise(pl.col("y"))
            .alias("y")
        )
        b = lc._predict(flipped, folds, SETTINGS)
        name = f"late_check_{year}"
        pa, pb = (x.filter(pl.col("fold") == name)["p_gbm_recal"] for x in (a, b))
        assert pa.equals(pb)


def _analyse(repo: Path, inputs: lc.LateCheckInputs) -> dict[str, Any]:
    rule, freeze = lc.verify_prepared(repo)
    folds = lc.late_check_folds(SPLITS, rule)
    return lc.analyse(inputs, rule=rule, freeze=freeze, folds=folds, settings=SETTINGS)[0]


def test_alignment_is_exact_and_decision_follows_the_rule(repo: Path) -> None:
    result = _analyse(repo, synthetic())
    assert result["n_matches"] == 600
    assert result["primary"]["decision"] == "CONFIRMED"
    assert result["primary"]["log_loss_diff"]["ci_high"] < 0
    assert set(result["secondary"]["per_year"]) == {"2023", "2024"}
    assert result["secondary"]["grand_slam_main"]["n"] > 0
    assert "sensitivity_without_rounds_into_draw" in result["secondary"]
    oracle = _analyse(repo, synthetic(elo_signal=0.0))
    assert oracle["primary"]["decision"] == "NOT_CONFIRMED"


@pytest.mark.parametrize("defect", ["missing", "extra", "duplicate", "label"])
def test_population_defects_abort(repo: Path, tmp_path: Path, defect: str) -> None:
    inputs = synthetic()
    elo = inputs.elo
    elo = {
        "missing": elo.slice(1),
        "extra": pl.concat([elo, elo.head(1).with_columns(pl.lit("zz").alias("match_key"))]),
        "duplicate": pl.concat([elo, elo.head(1)]),
        "label": elo.with_columns(
            pl.when(pl.int_range(pl.len()) == 0)
            .then(1 - pl.col("y"))
            .otherwise(pl.col("y"))
            .alias("y")
        ),
    }[defect]
    bad = lc.LateCheckInputs(frame=inputs.frame, labels=inputs.labels, elo=elo)
    with pytest.raises(lc.AlignmentError):
        _run(repo, tmp_path, lambda: bad)
    assert _looks(tmp_path) == 1
    assert lc._read_jsonl(tmp_path / "runs" / lc.AUDIT_NAME)[-1]["event"] == "failed"


def test_folds_must_match_the_rule(repo: Path) -> None:
    rule, _ = lc.verify_prepared(repo)
    other = SPLITS.model_copy(update={"late_check_years": (2024,)})
    with pytest.raises(lc.LateCheckRefusedError, match="folds"):
        lc.late_check_folds(other, rule)
