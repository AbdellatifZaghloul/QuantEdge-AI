"""Point-in-time guarantees of the Elo feature builder (Phase 2 scientific requirements)."""

from __future__ import annotations

import random
from datetime import date

import polars as pl
import pytest

from quantedge.core.asof import admissible_results
from quantedge.core.chronology import latest_instant
from quantedge.ratings.elo import EloConfig
from quantedge.ratings.features import elo_features
from tests.leakage.scenario import frame, mk, random_season

FEATURES = ["elo_a", "elo_b", "surface_elo_a", "surface_elo_b", "elo_n_a", "elo_n_b"]
CFG = EloConfig(k_mode="constant", k_constant=32)


def prepared(f: pl.DataFrame, *, update: pl.Expr | bool = True) -> pl.DataFrame:
    """Add the columns the feature builder expects from ``ratings.features.prepare``."""
    return f.with_columns(
        pl.lit("Hard").alias("surface"),
        (pl.lit(True) if update is True else update).alias("elo_update_baseline"),
        pl.lit(True).alias("elo_update_retirements"),
        pl.lit(True).alias("eval_eligible"),
        (pl.col("precedence_eligible") & ~pl.col("precedence_conflict")).alias("precedence_ok"),
        pl.col("round_ordinal").cast(pl.Int32).alias("source_match_num"),
    )


def flip(f: pl.DataFrame, keys: set[str]) -> pl.DataFrame:
    hit = pl.col("match_key").is_in(list(keys))
    return f.with_columns(
        pl.when(hit)
        .then(pl.when(pl.col("a_won")).then(pl.col("player_b_id")).otherwise(pl.col("player_a_id")))
        .otherwise(pl.col("winner_id"))
        .alias("winner_id"),
        pl.when(hit).then(~pl.col("a_won")).otherwise(pl.col("a_won")).alias("a_won"),
    )


def row(features: pl.DataFrame, key: str) -> tuple[object, ...]:
    return features.filter(pl.col("match_key") == key).select(FEATURES).row(0)


@pytest.fixture(scope="module")
def season() -> pl.DataFrame:
    return prepared(random_season(seed=5))


@pytest.fixture(scope="module")
def base(season: pl.DataFrame) -> pl.DataFrame:
    return elo_features(season, CFG)


def test_own_outcome_never_affects_own_ratings(season: pl.DataFrame, base: pl.DataFrame) -> None:
    for key in random.Random(1).sample(season["match_key"].to_list(), 20):
        assert row(elo_features(flip(season, {key}), CFG), key) == row(base, key)


def test_future_perturbation(season: pl.DataFrame, base: pl.DataFrame) -> None:
    for key in random.Random(2).sample(season["match_key"].to_list(), 15):
        admissible = set(admissible_results(season, key)["match_key"])
        perturbed = elo_features(flip(season, set(season["match_key"]) - admissible), CFG)
        assert row(perturbed, key) == row(base, key)


def test_same_day_isolation_and_naive_contrast() -> None:
    x, y, z, w = (f"sackmann:{i}" for i in range(4))
    day = date(2023, 3, 1)
    first = mk("sackmann:2023-0001", "R32", x, y, day, day)
    second = mk("sackmann:2023-0002", "R32", x, z, day, day)
    other = mk("sackmann:2023-0003", "R32", w, z, date(2023, 3, 2), date(2023, 3, 2))
    f = prepared(frame([first, second, other]))
    conservative = elo_features(f, CFG)
    assert row(conservative, second["match_key"])[:2] == (1500.0, 1500.0)
    assert row(elo_features(flip(f, {first["match_key"]}), CFG), second["match_key"]) == row(
        conservative, second["match_key"]
    )
    # The naive total order lets one same-day result leak into the other match: this is exactly the
    # exposure the sensitivity study measures, and why naive ordering is never the official variant.
    naive = elo_features(f, CFG, ordering="naive")
    naive_flipped = elo_features(flip(f, {first["match_key"]}), CFG, ordering="naive")
    assert row(naive, second["match_key"]) != row(naive_flipped, second["match_key"])


def test_excluded_results_never_update_ratings(season: pl.DataFrame) -> None:
    keys = season["match_key"].to_list()
    excluded = set(random.Random(3).sample(keys, len(keys) // 4))
    gated = season.with_columns(
        ~pl.col("match_key").is_in(list(excluded)).alias("elo_update_baseline")
    )
    a = elo_features(gated, CFG)
    b = elo_features(flip(gated, excluded), CFG)
    assert a.select("match_key", *FEATURES).equals(b.select("match_key", *FEATURES))


def test_deferred_knowability_hides_result_until_tournament_end() -> None:
    x, y, z = (f"sackmann:{i}" for i in range(3))
    qf = mk("sackmann:2023-0001", "QF", x, y, date(2023, 3, 1), date(2023, 3, 2))
    later_event = mk("sackmann:2023-0002", "R32", y, z, date(2023, 3, 6), date(2023, 3, 6))
    f = prepared(frame([qf, later_event]))
    visible = elo_features(f, CFG)
    assert row(visible, later_event["match_key"])[1] != 1500.0  # y's QF loss is known by Mar 6
    deferred = f.with_columns(
        pl.when(pl.col("match_key") == qf["match_key"])
        .then(pl.lit(latest_instant(date(2023, 3, 10))).cast(pl.Datetime("us", "UTC")))
        .otherwise(pl.col("knowable_at_utc"))
        .alias("knowable_at_utc"),
        pl.when(pl.col("match_key") == qf["match_key"])
        .then(False)
        .otherwise(pl.col("precedence_ok"))
        .alias("precedence_ok"),
    )
    hidden = elo_features(deferred, CFG)
    assert row(hidden, later_event["match_key"])[1] == 1500.0


def test_row_order_invariance(season: pl.DataFrame, base: pl.DataFrame) -> None:
    shuffled = season.sample(fraction=1.0, shuffle=True, seed=4)
    assert (
        elo_features(shuffled, CFG)
        .select("match_key", *FEATURES)
        .equals(base.select("match_key", *FEATURES))
    )


def test_antisymmetric_probabilities(base: pl.DataFrame) -> None:
    from quantedge.ratings.elo import expected_score  # noqa: PLC0415

    for ea, eb, p in base.select("elo_a", "elo_b", "p_elo").iter_rows():
        assert p == pytest.approx(expected_score(ea, eb))
        assert expected_score(eb, ea) == pytest.approx(1 - p)
