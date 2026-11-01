"""AT-17: replay scaffold leakage tests with toy stateful features (test-only, not models).

- future perturbation: flipping every inadmissible result leaves a target's features unchanged;
- truncation equivalence: dropping inadmissible results leaves a target's features unchanged;
- batch/row-order invariance: shuffling input rows changes nothing.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import Any

import polars as pl
import pytest

from quantedge.core.asof import Result, Target, admissible_results, replay
from tests.leakage.scenario import random_season

State = dict[str, float]


def apply_batch(state: State, batch: Sequence[Result]) -> State:
    """Non-additive toy update: every delta is computed from the pre-batch state."""
    pre = dict(state)
    new = dict(state)
    for r in batch:
        loser = r.player_b_id if r.winner_id == r.player_a_id else r.player_a_id
        w_pre, l_pre = pre.get(r.winner_id, 0.0), pre.get(loser, 0.0)
        new[r.winner_id] = new.get(r.winner_id, 0.0) + 1.0 + 0.5 * l_pre
        new[loser] = new.get(loser, 0.0) - 0.25 * w_pre
    return new


def read(state: State, target: Target) -> Mapping[str, Any]:
    return {"a_strength": state.get(target.player_a_id, 0.0),
            "b_strength": state.get(target.player_b_id, 0.0)}  # fmt: skip


def features(frame: pl.DataFrame) -> pl.DataFrame:
    return replay(frame, init=dict, apply_batch=apply_batch, copy_state=dict, read=read).sort(
        "match_key"
    )


@pytest.fixture(scope="module")
def season() -> pl.DataFrame:
    return random_season(seed=11)


@pytest.fixture(scope="module")
def baseline(season: pl.DataFrame) -> pl.DataFrame:
    return features(season)


def _flip(frame: pl.DataFrame, keys: set[str]) -> pl.DataFrame:
    flip = pl.col("match_key").is_in(list(keys))
    return frame.with_columns(
        pl.when(flip)
        .then(pl.when(pl.col("a_won")).then(pl.col("player_b_id")).otherwise(pl.col("player_a_id")))
        .otherwise(pl.col("winner_id"))
        .alias("winner_id"),
        pl.when(flip).then(~pl.col("a_won")).otherwise(pl.col("a_won")).alias("a_won"),
    )


def _row(frame: pl.DataFrame, key: str) -> tuple[object, ...]:
    return frame.filter(pl.col("match_key") == key).row(0)


def _targets(season: pl.DataFrame, n: int = 25) -> list[str]:
    keys = season["match_key"].to_list()
    return random.Random(3).sample(keys, n)


def test_features_exist_for_every_match(season: pl.DataFrame, baseline: pl.DataFrame) -> None:
    assert baseline.height == season.height
    assert baseline.filter((pl.col("a_strength") != 0) | (pl.col("b_strength") != 0)).height > 0


def test_future_perturbation_invariance(season: pl.DataFrame, baseline: pl.DataFrame) -> None:
    for key in _targets(season):
        admissible = set(admissible_results(season, key)["match_key"])
        inadmissible = set(season["match_key"]) - admissible  # includes the target itself
        perturbed = features(_flip(season, inadmissible))
        assert _row(perturbed, key) == _row(baseline, key), key


def test_truncation_equivalence(season: pl.DataFrame, baseline: pl.DataFrame) -> None:
    for key in _targets(season):
        keep = set(admissible_results(season, key)["match_key"]) | {key}
        truncated = features(season.filter(pl.col("match_key").is_in(list(keep))))
        assert _row(truncated, key) == _row(baseline, key), key


def test_row_order_invariance(season: pl.DataFrame, baseline: pl.DataFrame) -> None:
    shuffled = season.sample(fraction=1.0, shuffle=True, seed=99)
    assert features(shuffled).equals(baseline)


def test_perturbation_detects_a_leaky_engine(season: pl.DataFrame) -> None:
    """Power check: a deliberately leaky reader (sees all results) must fail the invariance."""

    def leaky(frame: pl.DataFrame) -> pl.DataFrame:
        everything = frame.with_columns(
            pl.lit(frame["prediction_cutoff_utc"].max()).alias("prediction_cutoff_utc")
        )
        return features(everything)

    key = _targets(season)[0]
    inadmissible = set(season["match_key"]) - set(admissible_results(season, key)["match_key"])
    a = leaky(season).filter(pl.col("match_key") == key).row(0)
    b = leaky(_flip(season, inadmissible)).filter(pl.col("match_key") == key).row(0)
    assert a != b
