"""Offline acquisition planner (dry run). Makes no network calls and spends no credits.

It turns the pre-registered protocol plus canonical data into a list of *scheduled* requests with
their documented credit cost. It cannot know what a request will *return*: bookmaker presence,
market depth and snapshot contents remain unknown until a request is actually made.

Documented costs (verified 2026-11-01): historical odds = 10 x markets x regions per call;
historical events = 1 per call (0 if empty). One historical odds call returns every in-season event
of the sport key at the snapshot, so all decisions falling in the same freshness slot share it.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import polars as pl

from quantedge.odds.coverage import load_coverage_table
from quantedge.odds.identity import load_tournament_aliases
from quantedge.odds.preregistration import Protocol
from quantedge.odds.theoddsapi import cost_historical_odds

HISTORICAL_EVENTS_COST = 1
FIVE_MINUTE_CADENCE_FROM = date(2022, 9, 1)  # documented: 10-min snapshots before Sept 2022


def _editions(
    matches: pl.DataFrame, tournaments: pl.DataFrame, eligibility: pl.DataFrame, protocol: Protocol
) -> list[dict[str, Any]]:
    aliases = load_tournament_aliases()
    coverage = {
        r["sport_key"]: datetime.fromisoformat(
            r["historical_from_utc"].replace("Z", "+00:00")
        ).date()
        for r in load_coverage_table()
    }
    pop = protocol.population
    frame = (
        matches.join(
            tournaments.select("tournament_id", "name", "level", "nominal_days"), on="tournament_id"
        )
        .join(eligibility.select("match_key", "eval_eligible"), on="match_key")
        .filter(
            pl.col("level").is_in(list(pop.canonical_levels))
            & pl.col("draw_category").str.ends_with("_main")
            & pl.col("tournament_start_date").is_between(
                pop.tournament_start_from, pop.tournament_start_to
            )
        )
    )
    editions = []
    for key in pop.sport_keys:
        pattern, _ = aliases[(pop.provider, key)]
        names = [n for n in frame["name"].unique().to_list() if n and re.search(pattern, n)]
        sub = frame.filter(pl.col("name").is_in(names) & (pl.col("match_day_hi") >= coverage[key]))
        for (tid, start, name, days), g in sub.group_by(
            ["tournament_id", "tournament_start_date", "name", "nominal_days"], maintain_order=True
        ):
            editions.append(
                {
                    "sport_key": key,
                    "tournament_id": tid,
                    "name": name,
                    "start_date": start,
                    "nominal_days": int(days),
                    "eligible_main_draw_matches": int(g["eval_eligible"].sum()),
                    "snapshot_cadence_minutes": 5 if start >= FIVE_MINUTE_CADENCE_FROM else 10,
                }
            )
    return sorted(editions, key=lambda e: (e["start_date"], e["sport_key"]))


def probe_plan(
    editions: list[dict[str, Any]],
    *,
    max_credits: int,
    markets: tuple[str, ...],
    regions: tuple[str, ...],
    prefer_years: tuple[int, ...] = (2022, 2021),
) -> dict[str, Any]:
    """Provider-availability probe: one historical odds snapshot per recent Slam edition."""
    per_call = cost_historical_odds(markets, regions)
    budget_calls = max_credits // per_call
    ranked = sorted(
        (e for e in editions if e["start_date"].year in prefer_years),
        key=lambda e: (prefer_years.index(e["start_date"].year), e["start_date"]),
    )
    chosen: list[dict[str, Any]] = []
    for edition in ranked:
        if len(chosen) >= budget_calls:
            break
        if any(c["sport_key"] == edition["sport_key"] for c in chosen):
            continue  # one snapshot per Slam first, for breadth
        # Day 2 of the main draw, 12:00 UTC: first-round matches still upcoming for most draws.
        when = datetime.combine(edition["start_date"] + timedelta(days=1), time(12), tzinfo=UTC)
        chosen.append(
            {
                "endpoint": f"/v4/historical/sports/{edition['sport_key']}/odds",
                "sport_key": edition["sport_key"],
                "edition": f"{edition['name']} {edition['start_date'].year}",
                "date_param_utc": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "markets": list(markets),
                "regions": list(regions),
                "credits": per_call,
            }
        )
    total = sum(c["credits"] for c in chosen)
    return {
        "purpose": [
            "which bookmaker keys price ATP Grand Slam singles in the region",
            "whether 'pinnacle' is present (pre-registered primary reference)",
            "presence and freshness of market-level last_update timestamps",
            "event naming (player aliases) and commence_time behaviour",
        ],
        "requests": chosen,
        "total_credits": total,
        "max_credits": max_credits,
        "within_budget": total <= max_credits,
        "returns": "unknown until executed; outcomes are never joined during the probe",
    }


def pilot_plan(
    editions: list[dict[str, Any]],
    protocol: Protocol,
    *,
    markets: tuple[str, ...],
    regions: tuple[str, ...],
    max_credits: int,
) -> dict[str, Any]:
    """Full pilot acquisition estimate (requires separate approval; never executed here)."""
    per_call = cost_historical_odds(markets, regions)
    slot = protocol.timing.primary_max_staleness_minutes
    rows = []
    for e in editions:
        events_calls = e["nominal_days"]  # one historical events call per tournament day
        upper = e["eligible_main_draw_matches"]  # worst case: one odds snapshot per match
        lower = e["nominal_days"]  # best case: one snapshot serves a whole day's decisions
        rows.append(
            {
                "edition": f"{e['name']} {e['start_date'].year}",
                "sport_key": e["sport_key"],
                "eligible_main_draw_matches": upper,
                "snapshot_cadence_minutes": e["snapshot_cadence_minutes"],
                "events_calls": events_calls,
                "events_credits_max": events_calls * HISTORICAL_EVENTS_COST,
                "odds_calls_lower": lower,
                "odds_calls_upper": upper,
                "odds_credits_lower": lower * per_call,
                "odds_credits_upper": upper * per_call,
            }
        )
    total_upper = sum(r["events_credits_max"] + r["odds_credits_upper"] for r in rows)
    total_lower = sum(r["events_credits_max"] + r["odds_credits_lower"] for r in rows)
    return {
        "editions": rows,
        "expected_eligible_population": sum(r["eligible_main_draw_matches"] for r in rows),
        "credits_lower_bound": total_lower,
        "credits_upper_bound": total_upper,
        "max_credits": max_credits,
        "within_budget": total_upper <= max_credits,
        "sharing_rule": (
            f"decisions (scheduled start - {protocol.timing.decision_lead_minutes:g} min) in the "
            f"same {slot:g}-minute slot share one historical odds snapshot taken at the slot start"
        ),
        "steps": [
            "1. historical events calls (<= 1 credit each) to learn scheduled start times per day",
            "2. group decision times into freshness slots offline",
            "3. one historical odds call per non-empty slot",
        ],
        "assumptions": [
            f"{per_call} credits per historical odds call "
            f"({len(markets)} market x {len(regions)} region)",
            "events calls cost 1 credit, 0 when empty (documented)",
            "nominal tournament days from reference/tournament_classes.csv",
            "the lower bound (one snapshot per day) is a floor only: it cannot satisfy the "
            "pre-registered freshness limit for most matches; the realistic figure needs step 1",
        ],
        "unknowns": [
            "bookmakers present per event and region; whether pinnacle is present",
            "share of events with complete two-way match-winner quotes",
            "actual scheduled start times (only known after events calls)",
            "how often market last_update lags the snapshot time",
        ],
        "returns": "scheduled requests only; returned quotes are unknown until executed",
    }


def build_plan(
    matches: pl.DataFrame,
    tournaments: pl.DataFrame,
    eligibility: pl.DataFrame,
    protocol: Protocol,
    *,
    probe_max_credits: int = 40,
    markets: tuple[str, ...] = ("h2h",),
    regions: tuple[str, ...] = ("eu",),
) -> dict[str, Any]:
    if probe_max_credits > 40:
        raise ValueError("the pre-approved probe ceiling is 40 credits")
    editions = _editions(matches, tournaments, eligibility, protocol)
    full = pilot_plan(editions, protocol, markets=markets, regions=regions, max_credits=0)
    return {
        "network_calls_made": 0,
        "credits_spent": 0,
        "probe": probe_plan(
            editions, max_credits=probe_max_credits, markets=markets, regions=regions
        ),
        "pilot_estimate": full,
        "approval_required": "every request listed here needs explicit owner approval first",
        "default_credit_ceiling": 0,
    }
