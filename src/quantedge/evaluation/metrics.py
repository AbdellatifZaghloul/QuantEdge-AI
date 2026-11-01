"""Probabilistic metrics, logistic fitting, recalibration and reliability summaries."""

from __future__ import annotations

from typing import Any

import numpy as np

EPS = 1e-6


def sigmoid(x: np.ndarray) -> np.ndarray:
    out: np.ndarray = 1.0 / (1.0 + np.exp(-x))
    return out


def logit(p: np.ndarray) -> np.ndarray:
    q = np.clip(p, EPS, 1 - EPS)
    out: np.ndarray = np.log(q / (1 - q))
    return out


def log_loss_terms(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    q = np.clip(p, EPS, 1 - EPS)
    out: np.ndarray = -(y * np.log(q) + (1 - y) * np.log(1 - q))
    return out


def brier_terms(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    out: np.ndarray = (p - y) ** 2
    return out


def accuracy_terms(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    """1 for a correct favourite, 0 for wrong, 0.5 for an exact 50/50 call."""
    out: np.ndarray = np.where(p == 0.5, 0.5, ((p > 0.5) == (y == 1)).astype(float))
    return out


def fit_logistic(
    features: np.ndarray, y: np.ndarray, *, intercept: bool, l2: float = 1e-8, max_iter: int = 100
) -> np.ndarray:
    """Maximum-likelihood logistic regression by Newton-Raphson (tiny ridge for stability)."""
    design = (
        np.column_stack([np.ones(len(y)), features])
        if intercept
        else np.asarray(features, dtype=float)
    )
    beta = np.zeros(design.shape[1])
    for _ in range(max_iter):
        p = sigmoid(design @ beta)
        grad = design.T @ (p - y) + l2 * beta
        w = p * (1 - p)
        hess = (design * w[:, None]).T @ design + l2 * np.eye(design.shape[1])
        step = np.linalg.solve(hess, grad)
        beta = beta - step
        if np.max(np.abs(step)) < 1e-10:
            break
    return beta


def calibration_intercept_slope(y: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    beta = fit_logistic(logit(p)[:, None], y, intercept=True)
    return float(beta[0]), float(beta[1])


def fit_slope_recalibration(y: np.ndarray, p: np.ndarray) -> float:
    """Slope-only recalibration ``p' = sigmoid(b * logit(p))``.

    No intercept: A/B orientation is random, so a calibrated model must stay antisymmetric.
    """
    return float(fit_logistic(logit(p)[:, None], y, intercept=False)[0])


def apply_slope(p: np.ndarray, slope: float) -> np.ndarray:
    return sigmoid(slope * logit(p))


def reliability_table(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict[str, Any]]:
    """Equal-mass bins by predicted probability."""
    order = np.argsort(p, kind="stable")
    rows = []
    for i, idx in enumerate(np.array_split(order, bins)):
        if len(idx) == 0:
            continue
        rows.append(
            {
                "bin": i,
                "n": len(idx),
                "mean_predicted": float(p[idx].mean()),
                "observed_frequency": float(y[idx].mean()),
            }
        )
    return rows


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    table = reliability_table(y, p, bins)
    n = sum(r["n"] for r in table)
    return float(
        sum(r["n"] * abs(r["mean_predicted"] - r["observed_frequency"]) for r in table) / n
    )


def summary(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    intercept, slope = calibration_intercept_slope(y, p)
    return {
        "n": float(len(y)),
        "log_loss": float(log_loss_terms(y, p).mean()),
        "brier": float(brier_terms(y, p).mean()),
        "accuracy": float(accuracy_terms(y, p).mean()),
        "ece": expected_calibration_error(y, p),
        "calibration_intercept": intercept,
        "calibration_slope": slope,
    }
