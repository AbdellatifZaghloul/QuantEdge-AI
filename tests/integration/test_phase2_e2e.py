"""End-to-end Phase 2 experiment on a synthetic multi-season snapshot (no licensed data).

Checks the registry/manifests, figures, the holdout seal (holdout seasons exist in the build but
never reach predictions), late-check accounting, and that Elo learns latent skill.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from quantedge.canonical.build import (
    RAW_MATCH_COLUMNS,
    RAW_PLAYER_COLUMNS,
    RAW_RANKING_COLUMNS,
    BuildResult,
    build,
)
from quantedge.core.splits import SplitConfig
from quantedge.experiments.phase2 import Phase2Config, run_phase2
from quantedge.experiments.registry import LateCheckLimitError, read_registry
from tests.fixtures.synthetic import _csv, match_row, materialise

YEARS = range(2005, 2015)  # 2013-2014 form the sealed holdout in this test
SPLITS = SplitConfig(
    burn_in_end=date(2004, 12, 31),
    development_start=date(2005, 1, 1),
    development_end=date(2012, 12, 31),
    holdout_start=date(2013, 1, 1),
    inner_test_years=(2009, 2010, 2011),
    late_check_years=(2012,),
    late_check_max_looks=2,
)
CONFIG = Phase2Config.model_validate(
    {
        "name": "synthetic_phase2",
        "model_version": "synthetic-v1",
        "seed": 11,
        "bootstrap_replicates": 200,
        "rank_staleness_days": 365,
        "rank_cap": 2000,
        "elo_grid": [
            {"k_mode": "constant", "k_constant": 24},
            {"k_mode": "fivethirtyeight", "k_c": 250},
        ],
        "surface_pseudo_counts": [0, 20, math.inf],
        "sensitivity": {
            "reference": {"k_mode": "fivethirtyeight", "k_c": 250},
            "decay": [{"decay_half_life_days": 365, "decay_grace_days": 60}],
            "retirement_policies": ["elo_update_retirements"],
            "orderings": ["naive"],
        },
    }
)


def synthetic_seasons() -> dict[str, bytes]:
    rng = random.Random(42)
    skill = {pid: rng.gauss(0, 1.2) for pid in range(500, 540)}
    surfaces = ["Hard", "Clay", "Grass"]
    files: dict[str, bytes] = {}
    rankings = []
    for year in YEARS:
        rows = []
        for event in range(14):
            monday = date(year, 1, 3) + timedelta(weeks=3 * event)
            monday -= timedelta(days=monday.weekday())
            field = rng.sample(sorted(skill), 8)
            order = sorted(skill, key=lambda p: -(skill[p] + rng.gauss(0, 0.3)))
            rankings += [
                {
                    "ranking_date": (monday - timedelta(days=7)).strftime("%Y%m%d"),
                    "rank": str(i + 1),
                    "player": str(p),
                    "points": "",
                }
                for i, p in enumerate(order)
            ]
            alive, num = field, 0
            for rnd in ("QF", "SF", "F"):
                winners = []
                for i in range(0, len(alive), 2):
                    a, b = alive[i], alive[i + 1]
                    p_a = 1 / (1 + math.exp(-(skill[a] - skill[b])))
                    w, l_ = (a, b) if rng.random() < p_a else (b, a)
                    score = "6-3 2-1 RET" if rng.random() < 0.03 else "6-4 6-4"
                    num += 1
                    rows.append(
                        match_row(
                            tourney_id=f"{year}-{event:04d}",
                            tourney_name=f"Syn {event}",
                            surface=surfaces[event % 3],
                            draw_size="8",
                            tourney_date=monday.strftime("%Y%m%d"),
                            round=rnd,
                            match_num=num,
                            winner_id=w,
                            loser_id=l_,
                            score=score,
                        )
                    )
                    winners.append(w)
                alive = winners
        files[f"atp/atp_matches_{year}.csv"] = _csv(rows, RAW_MATCH_COLUMNS)
    players = [
        {
            "player_id": str(p),
            "name_first": "Syn",
            "name_last": f"P{p}",
            "hand": "R",
            "dob": "",
            "ioc": "",
            "height": "",
            "wikidata_id": "",
        }
        for p in skill
    ]
    files["atp/atp_players.csv"] = _csv(players, RAW_PLAYER_COLUMNS)
    files["atp/atp_rankings_00s.csv"] = _csv(rankings, RAW_RANKING_COLUMNS)
    return files


@pytest.fixture(scope="module")
def synthetic_multi_season(tmp_path_factory: pytest.TempPathFactory) -> BuildResult:
    root = tmp_path_factory.mktemp("multi")
    snapshot, manifest, _ = materialise(root, synthetic_seasons())
    return build(snapshot, manifest, root / "processed")


def test_phase2_end_to_end(synthetic_multi_season: BuildResult, tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    run_id, run_dir, metrics = run_phase2(
        synthetic_multi_season.out_dir,
        SPLITS,
        CONFIG,
        "synthetic",
        runs_root=runs,
        repo_root=tmp_path,
        kind="inner",
    )
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == run_id and manifest["seed"] == 11
    assert manifest["data"]["build_id"] == synthetic_multi_season.build_id
    assert manifest["data"]["view"] == "development"
    assert len(manifest["eligibility"]["content_sha256"]) == 64
    assert [f["fold"] for f in manifest["splits"]["folds"]] == [
        "inner_2009",
        "inner_2010",
        "inner_2011",
    ]
    for figure in ("reliability_recalibrated.png", "reliability_raw.png", "log_loss_by_fold.png"):
        assert (run_dir / "figures" / figure).stat().st_size > 1000
    assert read_registry(runs)[0]["run_id"] == run_id

    test = pl.read_parquet(run_dir / "test_predictions.parquet")
    holdout_keys = set(
        synthetic_multi_season.tables["matches"].filter(
            pl.col("tournament_start_date") >= SPLITS.holdout_start
        )["match_key"]
    )
    assert holdout_keys and not (holdout_keys & set(test["match_key"]))
    assert test["tournament_start_date"].min() >= date(2009, 1, 1)
    assert test["tournament_start_date"].max() <= date(2011, 12, 31)

    pooled = metrics["pooled"]
    assert pooled["elo_overall"]["log_loss"] < pooled["coin"]["log_loss"]  # Elo learns latent skill
    assert {m: pooled[m]["n"] for m in pooled} == {
        m: pooled["coin"]["n"] for m in pooled
    }  # same population
    assert "ordering_naive" in metrics["sensitivity"]["variants_vs_reference"]


def test_late_check_is_counted_and_capped(
    synthetic_multi_season: BuildResult, tmp_path: Path
) -> None:
    runs = tmp_path / "runs"
    for look in (1, 2):
        _, run_dir, _ = run_phase2(
            synthetic_multi_season.out_dir,
            SPLITS,
            CONFIG,
            "synthetic",
            runs_root=runs,
            repo_root=tmp_path,
            kind="late_check",
            note=f"look {look}",
        )
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["late_check_look"] == look
    with pytest.raises(LateCheckLimitError):
        run_phase2(
            synthetic_multi_season.out_dir,
            SPLITS,
            CONFIG,
            "synthetic",
            runs_root=runs,
            repo_root=tmp_path,
            kind="late_check",
        )
