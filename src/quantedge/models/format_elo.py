"""Format-aware Elo (diagnostic baseline ``phase5-elo-format-diag-v1``; not a frozen model).

Elo probabilities are learned almost entirely from best-of-3 matches. Treating them as best-of-3
match probabilities, the implied per-set win probability ``s`` solves ``p3 = s^2 (3 - 2s)``; the
best-of-5 probability is then ``p5 = s^3 (10 - 15 s + 6 s^2)`` (independent, identically
distributed sets). No parameter is fitted here; per-fold slope recalibration happens downstream,
exactly as for the frozen Elo baseline.
"""

from __future__ import annotations

import numpy as np


def best_of_three(s: np.ndarray) -> np.ndarray:
    out: np.ndarray = s * s * (3.0 - 2.0 * s)
    return out


def best_of_five(s: np.ndarray) -> np.ndarray:
    out: np.ndarray = s**3 * (10.0 - 15.0 * s + 6.0 * s * s)
    return out


def set_probability(p3: np.ndarray, *, iterations: int = 60) -> np.ndarray:
    """Invert ``best_of_three`` by bisection (monotone on [0, 1]); exact to ~1e-15."""
    p3 = np.asarray(p3, dtype=float)
    lo, hi = np.zeros_like(p3), np.ones_like(p3)
    for _ in range(iterations):
        mid = 0.5 * (lo + hi)
        below = best_of_three(mid) < p3
        lo = np.where(below, mid, lo)
        hi = np.where(below, hi, mid)
    out: np.ndarray = 0.5 * (lo + hi)
    return out


def format_adjusted(p: np.ndarray, best_of: np.ndarray) -> np.ndarray:
    """Best-of-5 matches get the implied best-of-5 probability; others keep ``p``."""
    p = np.asarray(p, dtype=float)
    p5 = best_of_five(set_probability(p))
    out: np.ndarray = np.where(np.asarray(best_of) == 5, p5, p)
    return out
