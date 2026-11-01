"""Provider coverage and cost assessment from the documented coverage table (no network calls).

Counts how many canonical matches fall inside each documented historical coverage window, split
into development (eligible for evaluation) and holdout (integrity counts only), and estimates the
credit cost of a historical acquisition plan.
"""

from __future__ import annotations

import csv
import re
from datetime import date, datetime
from importlib import resources
from typing import Any

import polars as pl

from quantedge.odds.identity import load_tournament_aliases
from quantedge.odds.theoddsapi import cost_historical_odds


def load_coverage_table() -> list[dict[str, str]]:
    text = (
        resources.files("quantedge.reference")
        .joinpath("odds_api_tennis_coverage.csv")
        .read_text(encoding="utf-8")
    )
    return list(csv.DictReader(text.splitlines()))


def assess_coverage(
    matches: pl.DataFrame,
    tournaments: pl.DataFrame,
    eligibility: pl.DataFrame,
    *,
    development_end: date,
    snapshots_per_day: int = 1,
    markets: tuple[str, ...] = ("h2h",),
    regions: tuple[str, ...] = ("eu",),
    provider: str = "the_odds_api",
) -> dict[str, Any]:
    aliases = load_tournament_aliases()
    frame = matches.join(
        tournaments.select("tournament_id", "name", "level", "nominal_days"), on="tournament_id"
    ).join(eligibility.select("match_key", "eval_eligible"), on="match_key")
    per_key: list[dict[str, Any]] = []
    for row in load_coverage_table():
        key = row["sport_key"]
        start = datetime.fromisoformat(row["historical_from_utc"].replace("Z", "+00:00")).date()
        pattern, levels = aliases[(provider, key)]
        names = [n for n in frame["name"].unique().to_list() if n and re.search(pattern, n)]
        covered = frame.filter(
            pl.col("name").is_in(names)
            & (pl.col("level").is_in(list(levels)) if levels else pl.lit(True))
            & (pl.col("match_day_hi") >= start)
            & pl.col("draw_category").str.ends_with("_main")
        )
        dev = covered.filter(pl.col("tournament_start_date") <= development_end)
        hold = covered.filter(pl.col("tournament_start_date") > development_end)
        editions = covered.select("tournament_id", "nominal_days").unique()
        days = int(editions["nominal_days"].sum()) if editions.height else 0
        per_key.append(
            {
                "sport_key": key,
                "historical_from": str(start),
                "canonical_names": names,
                "editions_in_snapshot": editions.height,
                "main_draw_matches_dev": dev.height,
                "eval_eligible_dev": int(dev["eval_eligible"].sum()),
                "main_draw_matches_holdout_integrity_count": hold.height,
                "tournament_days": days,
                "estimated_credits": days
                * snapshots_per_day
                * cost_historical_odds(markets, regions),
            }
        )
    totals = {
        k: sum(int(r[k]) for r in per_key)
        for k in (
            "main_draw_matches_dev",
            "eval_eligible_dev",
            "main_draw_matches_holdout_integrity_count",
            "tournament_days",
            "estimated_credits",
        )
    }
    return {
        "assumptions": {
            "snapshots_per_day": snapshots_per_day,
            "markets": list(markets),
            "regions": list(regions),
            "credits_per_historical_call": cost_historical_odds(markets, regions),
            "note": "Coverage counts use canonical data only; actual bookmaker/market depth per "
            "event is unverified until a paid probe.",
        },
        "per_sport_key": per_key,
        "totals": totals,
    }
