"""Bookmaker margin removal ("de-vigging") for mutually exclusive, exhaustive outcomes.

Notation: decimal odds ``o_i > 1``, implied probabilities ``q_i = 1 / o_i``, booksum
``Q = sum(q_i)`` (overround = ``Q - 1``).

- **Proportional** (multiplicative): ``p_i = q_i / Q``. Spreads the margin in proportion to ``q_i``.
- **Power**: ``p_i = q_i ** k`` with ``k`` chosen so ``sum(p_i) = 1`` (``k > 1`` when ``Q > 1``).
  Takes relatively more margin from longshots, a standard way to model the favourite-longshot bias.
- **Shin** (Shin 1993; Jullien & Salanie 1994): pricing against a fraction ``z`` of insider
  money. ``p_i = (sqrt(z^2 + 4 (1 - z) q_i^2 / Q) - z) / (2 (1 - z))`` with ``z`` solved so that
  ``sum(p_i) = 1``. Also shifts margin toward longshots, from an explicit economic model.

Which method best matches outcomes is an empirical question for the development data (P3); all
three are provided so the choice can be made and reported without changing code. Inputs whose
booksum is below 1 (an "underround", e.g. best prices across books) are refused by power and Shin
and only flagged by proportional.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

Method = Literal["proportional", "power", "shin"]
METHODS: tuple[Method, ...] = ("proportional", "power", "shin")
_TOL = 1e-12
_FAIR_TOL = 1e-9  # booksums this close to 1 are treated as margin-free (float rounding)
_MAX_ITER = 200


class DevigError(ValueError):
    """Raised for invalid odds or inputs a method cannot handle."""


@dataclass(frozen=True)
class DevigResult:
    method: Method
    probabilities: tuple[float, ...]
    booksum: float
    parameter: float  # power k, Shin z, or 1.0 for proportional
    underround: bool

    @property
    def overround(self) -> float:
        return self.booksum - 1.0


def implied_probabilities(odds: Sequence[float]) -> tuple[float, ...]:
    if len(odds) < 2:
        raise DevigError("need at least two outcomes")
    for o in odds:
        if not math.isfinite(o) or o <= 1.0:
            raise DevigError(f"decimal odds must be finite and > 1, got {o!r}")
    return tuple(1.0 / o for o in odds)


def booksum(odds: Sequence[float]) -> float:
    return math.fsum(implied_probabilities(odds))


def proportional(odds: Sequence[float]) -> DevigResult:
    q = implied_probabilities(odds)
    total = math.fsum(q)
    return DevigResult(
        "proportional", tuple(x / total for x in q), total, 1.0, total < 1.0 - _FAIR_TOL
    )


def _bisect(fn: Callable[[float], float], lo: float, hi: float) -> float:
    """Root of a monotone function on ``[lo, hi]`` by bisection."""
    f_lo = fn(lo)
    for _ in range(_MAX_ITER):
        mid = (lo + hi) / 2
        f_mid = fn(mid)
        if abs(f_mid) < _TOL or hi - lo < _TOL:
            return mid
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2


def power(odds: Sequence[float]) -> DevigResult:
    q = implied_probabilities(odds)
    total = math.fsum(q)
    if total < 1.0 - _FAIR_TOL:
        raise DevigError("power method requires a booksum >= 1 (underround given)")
    if abs(total - 1.0) <= _FAIR_TOL:
        return DevigResult("power", tuple(x / total for x in q), total, 1.0, False)
    # sum(q_i^k) is strictly decreasing in k; find k >= 1 with sum = 1.
    hi = 2.0
    while math.fsum(x**hi for x in q) > 1.0:
        hi *= 2
    k = _bisect(lambda k: math.fsum(x**k for x in q) - 1.0, 1.0, hi)
    p = tuple(x**k for x in q)
    norm = math.fsum(p)
    return DevigResult("power", tuple(x / norm for x in p), total, k, False)


def _shin_probabilities(q: Sequence[float], total: float, z: float) -> tuple[float, ...]:
    return tuple((math.sqrt(z * z + 4 * (1 - z) * x * x / total) - z) / (2 * (1 - z)) for x in q)


def shin(odds: Sequence[float]) -> DevigResult:
    q = implied_probabilities(odds)
    total = math.fsum(q)
    if total < 1.0 - _FAIR_TOL:
        raise DevigError("Shin's method requires a booksum >= 1 (underround given)")
    if abs(total - 1.0) <= _FAIR_TOL:
        return DevigResult("shin", tuple(x / total for x in q), total, 0.0, False)
    # sum of Shin probabilities is strictly decreasing in z on [0, 1); find the root.
    z = _bisect(lambda z: math.fsum(_shin_probabilities(q, total, z)) - 1.0, 0.0, 1.0 - 1e-9)
    p = _shin_probabilities(q, total, z)
    norm = math.fsum(p)
    return DevigResult("shin", tuple(x / norm for x in p), total, z, False)


def devig(odds: Sequence[float], method: Method) -> DevigResult:
    if method == "proportional":
        return proportional(odds)
    if method == "power":
        return power(odds)
    if method == "shin":
        return shin(odds)
    raise DevigError(f"unknown method {method!r}")
