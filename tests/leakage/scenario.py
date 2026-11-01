"""Builders for canonical-shaped match frames used by point-in-time tests."""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

import polars as pl

from quantedge.canonical.reference import load_reference
from quantedge.core.chronology import earliest_instant, latest_instant
from quantedge.core.ids import match_key, orient

UTC_DT = pl.Datetime("us", "UTC")


def mk(
    tournament: str,
    round_code: str,
    winner: str,
    loser: str,
    day_lo: date,
    day_hi: date,
    *,
    season: int = 2023,
    conflict: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    info = load_reference().rounds[round_code]
    key = match_key(
        tournament_id=tournament, season=season, draw_category=f"tour_{info.stage}",
        round_code=round_code, player_1=winner, player_2=loser,
    )  # fmt: skip
    a, b = orient(key, winner, loser)
    row = {
        "match_key": key, "tournament_id": tournament, "round": round_code,
        "round_ordinal": info.ordinal, "precedence_eligible": info.precedence_eligible,
        "precedence_conflict": conflict, "player_a_id": a, "player_b_id": b,
        "winner_id": winner, "a_won": a == winner, "outcome_type": "completed",
        "score_raw": "6-4 6-4", "score_status": "ok", "sets_a": 2 if a == winner else 0,
        "sets_b": 0 if a == winner else 2, "games_a": 12 if a == winner else 8,
        "games_b": 8 if a == winner else 12, "minutes": 90, "match_day_lo": day_lo,
        "match_day_hi": day_hi, "prediction_cutoff_utc": earliest_instant(day_lo),
        "knowable_at_utc": latest_instant(day_hi), "tournament_start_date": day_lo,
    }  # fmt: skip
    row.update(extra or {})
    return row


def frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    return pl.DataFrame(rows).with_columns(
        pl.col("prediction_cutoff_utc").cast(UTC_DT),
        pl.col("knowable_at_utc").cast(UTC_DT),
        pl.col("round_ordinal").cast(pl.Int16),
    )


def random_season(seed: int, events: int = 24, pool: int = 24) -> pl.DataFrame:
    """Knockout events (QF/SF/F) on a weekly calendar with one-week day bounds.

    Some events overlap (two per week) so cross-event knowability and the overlay are exercised.
    """
    rng = random.Random(seed)
    ref = load_reference()
    players = [f"sackmann:{1000 + i}" for i in range(pool)]
    rows: list[dict[str, Any]] = []
    start = date(2023, 1, 2)
    for e in range(events):
        monday = start + timedelta(days=7 * (e // 2))
        tid = f"sackmann:2023-{e:04d}"
        field = rng.sample(players, 8)
        alive = field
        for round_code in ("QF", "SF", "F"):
            lo, hi = ref.bounds("one_week", round_code)
            winners = []
            for i in range(0, len(alive), 2):
                w, l_ = (alive[i], alive[i + 1]) if rng.random() < 0.5 else (alive[i + 1], alive[i])
                rows.append(
                    mk(
                        tid,
                        round_code,
                        w,
                        l_,
                        monday + timedelta(days=lo),
                        monday + timedelta(days=hi),
                    )
                )
                winners.append(w)
            alive = winners
    return frame(rows)
