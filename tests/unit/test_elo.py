"""Elo mathematics, batch semantics, overlay isolation, decay and surface pooling."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

import pytest

from quantedge.core.asof import Result
from quantedge.ratings.elo import (
    EloConfig,
    EloState,
    Track,
    apply_batch,
    decayed_rating,
    expected_score,
    k_factor,
    pooled_surface_rating,
)

T0 = datetime(2020, 1, 1, tzinfo=UTC)


def result(
    key: str, a: str, b: str, winner: str, surface: str | None = "Hard", when: datetime = T0
) -> Result:
    return Result(key, "t", 70, a, b, winner, when, True, surface, when)


def test_expected_score() -> None:
    assert expected_score(1500, 1500) == 0.5
    assert expected_score(1900, 1500) == pytest.approx(10 / 11)
    for ra, rb in ((1500, 1600), (2100, 1800)):
        assert expected_score(ra, rb) + expected_score(rb, ra) == pytest.approx(1.0)


def test_k_factor() -> None:
    assert k_factor(10, EloConfig(k_mode="constant", k_constant=32)) == 32
    cfg = EloConfig(k_mode="fivethirtyeight", k_c=250, k_offset=5, k_shape=0.4)
    assert k_factor(0, cfg) == pytest.approx(250 / 5**0.4)
    assert k_factor(0, cfg) > k_factor(10, cfg) > k_factor(100, cfg)


def test_single_update_constant_k_is_zero_sum() -> None:
    state = apply_batch(
        EloState(EloConfig(k_mode="constant", k_constant=32)), [result("m", "a", "b", "a")]
    )
    assert state.overall_of("a").rating == pytest.approx(1516)
    assert state.overall_of("b").rating == pytest.approx(1484)
    assert state.overall_of("a").n == 1 and state.surface_of("a", "Hard").n == 1
    assert state.surface_of("a", "Clay").n == 0


def test_unknown_surface_updates_overall_only() -> None:
    state = apply_batch(EloState(EloConfig()), [result("m", "a", "b", "a", surface=None)])
    assert state.overall_of("a").n == 1
    assert all(state.surface_of("a", s).n == 0 for s in ("Hard", "Clay", "Grass", "Carpet"))


def test_batch_is_simultaneous_and_order_independent() -> None:
    cfg = EloConfig(k_mode="constant", k_constant=32)
    batch = [result("m1", "a", "b", "a"), result("m2", "a", "c", "c"), result("m3", "b", "c", "b")]
    reference = apply_batch(EloState(cfg), batch)
    # Every delta uses pre-batch ratings (all 1500): a gets +16 - 16 = 0.
    assert reference.overall_of("a").rating == pytest.approx(1500)
    for seed in range(5):
        shuffled = batch[:]
        random.Random(seed).shuffle(shuffled)
        other = apply_batch(EloState(cfg), shuffled)
        for p in "abc":
            assert other.overall_of(p) == reference.overall_of(p)


def test_fork_never_mutates_base() -> None:
    base = apply_batch(EloState(EloConfig()), [result("m", "a", "b", "a")])
    before = base.overall_of("a")
    view = apply_batch(base.fork(), [result("m2", "a", "c", "c")])
    assert view.overall_of("a") != before
    assert base.overall_of("a") == before and "c" not in base.rated_players()


def test_decay() -> None:
    track = Track(1700.0, 10, T0)
    later = T0 + timedelta(days=365)
    assert decayed_rating(track, later, EloConfig()) == 1700.0
    assert decayed_rating(track, later, EloConfig(decay_half_life_days=365)) == pytest.approx(1600)
    graced = EloConfig(decay_half_life_days=365, decay_grace_days=60)
    assert decayed_rating(track, T0 + timedelta(days=59), graced) == 1700.0
    assert decayed_rating(track, T0 + timedelta(days=425), graced) == pytest.approx(1600)


def test_surface_pooling() -> None:
    assert pooled_surface_rating(1600, 10, 1500, 0) == 1600
    assert pooled_surface_rating(1600, 10, 1500, math.inf) == 1500
    assert pooled_surface_rating(1600, 10, 1500, 10) == pytest.approx(1550)
    assert pooled_surface_rating(1600, 0, 1500, 5) == 1500  # no surface history -> overall


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        EloConfig(k_constant=0)
    with pytest.raises(ValueError):
        EloConfig(decay_half_life_days=-1)
    assert EloConfig().label() == "538c250o5s0.4"
    assert EloConfig(decay_half_life_days=365, decay_grace_days=60).label().endswith("_h365g60")
