"""Reliability diagrams and per-fold metric plots (matplotlib, headless)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt

from quantedge.evaluation.metrics import reliability_table

FOOTNOTE = (
    "Data: Jeff Sackmann / Tennis Abstract (CC BY-NC-SA 4.0), via tennis-sackmann-archive; "
    "aggregated by QuantEdge AI. Non-commercial."
)


def reliability_diagram(
    y: Any, probabilities: Mapping[str, Any], path: Path, *, title: str, bins: int = 10
) -> list[dict[str, Any]]:
    fig, ax = plt.subplots(figsize=(6.4, 6.0), dpi=110)
    ax.plot([0, 1], [0, 1], linestyle="--", color="grey", linewidth=1, label="perfect calibration")
    tables = []
    for name, p in probabilities.items():
        table = reliability_table(y, p, bins)
        tables.append({"model": name, "bins": table})
        ax.plot(
            [r["mean_predicted"] for r in table],
            [r["observed_frequency"] for r in table],
            marker="o",
            linewidth=1.5,
            label=name,
        )
    ax.set_xlabel("mean predicted P(player A wins)")
    ax.set_ylabel("observed frequency")
    ax.set_title(title, fontsize=10)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    fig.text(0.01, 0.005, FOOTNOTE, fontsize=5.5, color="grey")
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return tables


def fold_metric_plot(
    folds: Sequence[str], series: Mapping[str, Sequence[float]], path: Path, *, metric: str
) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=110)
    for name, values in series.items():
        ax.plot(list(folds), list(values), marker="o", linewidth=1.5, label=name)
    ax.set_ylabel(metric)
    ax.set_xlabel("test fold")
    ax.tick_params(axis="x", rotation=45)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.text(0.01, 0.005, FOOTNOTE, fontsize=5.5, color="grey")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
