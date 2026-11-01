"""De-vig methods: known values, oracle agreement, and mathematical properties."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quantedge.odds.devig import (
    METHODS,
    DevigError,
    booksum,
    devig,
    implied_probabilities,
    power,
    proportional,
    shin,
)


def test_proportional_known_values() -> None:
    r = proportional([1.5, 2.7])
    assert r.booksum == pytest.approx(1 / 1.5 + 1 / 2.7)
    assert r.probabilities == pytest.approx((0.642857142857, 0.357142857143))
    assert r.overround == pytest.approx(r.booksum - 1)


def test_shin_matches_independent_oracle() -> None:
    oracle = pytest.importorskip("shin")
    for odds in ([1.5, 2.7], [1.2, 4.5], [1.91, 1.91], [2.0, 2.0, 4.0], [1.05, 11.0]):
        expected = oracle.calculate_implied_probabilities(odds)
        assert shin(odds).probabilities == pytest.approx(tuple(expected), abs=1e-9)


def test_power_known_value_and_parameter() -> None:
    r = power([1.5, 2.7])
    assert r.parameter > 1
    assert sum(q**r.parameter for q in implied_probabilities([1.5, 2.7])) == pytest.approx(1.0)


def test_invalid_inputs() -> None:
    for bad in ([1.0, 2.0], [0.9, 3.0], [math.nan, 2.0], [2.0]):
        with pytest.raises(DevigError):
            proportional(bad)
    underround = [2.2, 2.2]  # booksum 0.909
    assert proportional(underround).underround
    for method in ("power", "shin"):
        with pytest.raises(DevigError, match="underround"):
            devig(underround, method)  # type: ignore[arg-type]
    with pytest.raises(DevigError):
        devig([1.5, 2.7], "median")  # type: ignore[arg-type]


two_way = st.tuples(
    st.floats(min_value=1.02, max_value=30.0), st.floats(min_value=1.02, max_value=30.0)
).filter(lambda o: booksum(o) >= 1.0)
multi_way = st.lists(st.floats(min_value=1.02, max_value=40.0), min_size=2, max_size=6).filter(
    lambda o: booksum(o) >= 1.0
)


@settings(max_examples=300, deadline=None)
@given(multi_way)
def test_probabilities_are_a_distribution_and_order_preserving(odds: list[float]) -> None:
    for method in METHODS:
        p = devig(odds, method).probabilities
        assert math.fsum(p) == pytest.approx(1.0, abs=1e-9)
        assert all(0 < x < 1 for x in p)
        for i in range(len(odds)):
            for j in range(len(odds)):
                if odds[i] < odds[j] - 1e-12:
                    assert p[i] >= p[j] - 1e-12  # shorter odds never get lower probability


@settings(max_examples=300, deadline=None)
@given(two_way)
def test_longshot_adjustment_direction(odds: tuple[float, float]) -> None:
    """With a margin, power and Shin take more margin from the longshot than proportional does."""
    fav = 0 if odds[0] <= odds[1] else 1
    if booksum(odds) - 1 < 1e-6 or abs(odds[0] - odds[1]) < 1e-6:
        return
    base = proportional(odds).probabilities[fav]
    assert power(odds).probabilities[fav] >= base - 1e-12
    assert shin(odds).probabilities[fav] >= base - 1e-12


@settings(max_examples=200, deadline=None)
@given(st.lists(st.floats(min_value=0.02, max_value=0.98), min_size=2, max_size=5))
def test_fair_book_is_returned_unchanged(raw: list[float]) -> None:
    total = math.fsum(raw)
    fair = [total / x for x in raw]  # decimal odds of a book with exactly zero margin
    if any(o <= 1.0 for o in fair):
        return
    for method in METHODS:
        assert devig(fair, method).probabilities == pytest.approx(
            [x / total for x in raw], abs=1e-9
        )
