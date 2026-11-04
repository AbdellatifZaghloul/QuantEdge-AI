"""Lazy date-filtered loader: holdout rows never materialize (synthetic Parquet only)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from quantedge.core.dataview import DataView, HoldoutAccessError
from quantedge.core.lazy_tables import load_development_tables
from quantedge.core.splits import SplitConfig
from quantedge.experiments import phase5_late_check as lc
from quantedge.experiments.phase5 import INNER_TABLES

SPLITS = SplitConfig(
    burn_in_end=date(2016, 12, 31),
    development_start=date(2017, 1, 1),
    development_end=date(2024, 12, 31),
    holdout_start=date(2025, 1, 1),
    inner_test_years=(),
    late_check_years=(2023, 2024),
    late_check_max_looks=3,
)
YEARS = (2022, 2023, 2024, 2025, 2026)


def _build(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    keys = [f"{y}-{i}" for y in YEARS for i in range(3)]
    starts = [date(y, 6, 1) for y in YEARS for _ in range(3)]
    pl.DataFrame(
        {"match_key": keys, "tournament_start_date": starts, "a_won": [True] * 15}
    ).write_parquet(root / "matches.parquet")
    pl.DataFrame(
        {"tournament_id": [str(y) for y in YEARS], "start_date": [date(y, 6, 1) for y in YEARS]}
    ).write_parquet(root / "tournaments.parquet")
    pl.DataFrame({"ranking_date": [date(y, 1, 1) for y in YEARS], "rank": [1] * 5}).write_parquet(
        root / "rankings.parquet"
    )
    for child in ("match_entry", "match_stats"):
        pl.DataFrame({"match_key": keys * 2, "v": list(range(30))}).write_parquet(
            root / f"{child}.parquet"
        )
    pl.DataFrame({"player_id": ["p"]}).write_parquet(root / "players.parquet")
    return root


def test_matches_dataview_exactly_and_excludes_holdout(tmp_path: Path) -> None:
    build = _build(tmp_path / "b")
    lazy = load_development_tables(build, SPLITS, INNER_TABLES)
    view = DataView(load_tables_subset(build), SPLITS, mode="development")
    for name in INNER_TABLES:
        assert lazy[name].equals(view.table(name, end=SPLITS.development_end)), name
    assert lazy["matches"]["tournament_start_date"].max() == date(2024, 6, 1)
    for child in ("match_entry", "match_stats"):
        assert not lazy[child]["match_key"].str.starts_with("2025").any()
        assert not lazy[child]["match_key"].str.starts_with("2026").any()
        assert lazy[child].height == 18


def load_tables_subset(build: Path) -> dict[str, pl.DataFrame]:
    return {n: pl.read_parquet(build / f"{n}.parquet") for n in INNER_TABLES}


def test_earlier_end_cut(tmp_path: Path) -> None:
    out = load_development_tables(
        _build(tmp_path / "b"), SPLITS, INNER_TABLES, end=date(2022, 12, 31)
    )
    assert out["matches"].height == 3 and out["match_stats"].height == 6


def test_holdout_end_is_refused(tmp_path: Path) -> None:
    with pytest.raises(HoldoutAccessError):
        load_development_tables(_build(tmp_path / "b"), SPLITS, INNER_TABLES, end=date(2025, 1, 1))
    with pytest.raises(HoldoutAccessError):
        load_development_tables(_build(tmp_path / "b2"), SPLITS, ["players"])


def test_no_eager_full_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    build = _build(tmp_path / "b")

    def _no(*_: Any, **__: Any) -> Any:
        raise AssertionError("eager full-table read")

    monkeypatch.setattr(pl, "read_parquet", _no)
    load_development_tables(build, SPLITS, INNER_TABLES)


def test_late_check_loader_feeds_model_no_holdout_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build = _build(tmp_path / "b")
    runs = tmp_path / "runs"
    (runs / "base").mkdir(parents=True)
    pl.DataFrame({"match_key": ["2023-0"], "y": [1], "p_elo_overall_recal": [0.5]}).write_parquet(
        runs / "base" / "p.parquet"
    )
    seen: dict[str, Any] = {}

    def fake_build_frame(
        tables: dict[str, pl.DataFrame], _: Any
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        seen.update(tables)
        return tables["matches"], tables["matches"]

    monkeypatch.setattr(lc, "build_frame", fake_build_frame)
    monkeypatch.setattr(lc, "category_labels", lambda t, e: t["matches"])
    monkeypatch.setattr(lc, "load_phase5_config", lambda p: None)
    rule = {
        "baseline": {
            "phase2_late_check_run_id": "base",
            "file": "p.parquet",
            "column": "p_elo_overall_recal",
        }
    }
    lc.make_loader(build, SPLITS, repo_root=tmp_path, runs_root=runs, rule=rule)()
    assert set(seen) == set(INNER_TABLES)
    assert seen["matches"]["tournament_start_date"].max() <= SPLITS.development_end
    for child in ("match_entry", "match_stats"):
        assert set(seen[child]["match_key"]) <= set(seen["matches"]["match_key"])
