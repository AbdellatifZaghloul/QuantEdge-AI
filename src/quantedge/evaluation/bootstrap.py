"""Block bootstrap over tournament-week blocks (VALIDATION.md §4)."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np


def block_bootstrap_means(
    blocks: np.ndarray,
    values: Mapping[str, np.ndarray],
    *,
    replicates: int,
    seed: int,
    level: float = 0.95,
) -> dict[str, dict[str, float]]:
    """Percentile CIs for per-row means, resampling whole blocks with replacement.

    ``values`` maps a name to per-row terms (e.g. log-loss terms, or paired differences). The
    statistic for each replicate is ``sum(terms in drawn blocks) / count(rows in drawn blocks)``.
    """
    unique, inverse = np.unique(blocks, return_inverse=True)
    n_blocks = len(unique)
    counts = np.bincount(inverse, minlength=n_blocks).astype(float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n_blocks, size=(replicates, n_blocks))
    weights = np.zeros((replicates, n_blocks))
    for r in range(replicates):
        weights[r] = np.bincount(draws[r], minlength=n_blocks)
    denom = weights @ counts
    alpha = (1 - level) / 2
    out: dict[str, dict[str, float]] = {}
    for name, terms in values.items():
        sums = np.bincount(inverse, weights=terms, minlength=n_blocks)
        stats = (weights @ sums) / denom
        out[name] = {
            "estimate": float(terms.mean()),
            "ci_low": float(np.quantile(stats, alpha)),
            "ci_high": float(np.quantile(stats, 1 - alpha)),
            "blocks": float(n_blocks),
        }
    return out
