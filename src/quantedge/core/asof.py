"""Point-in-time information sets and a stateful replay scaffold (ARCHITECTURE.md §5.5-5.6).

For a target match *m* with cutoff *t*, a past result *r* is admissible iff

- (K) ``knowable_at(r) <= t``; or
- (P) *r* is in the same tournament edition, involves a participant of *m*, both rounds are
  precedence-eligible and conflict-free, and ``round_ordinal(r) < round_ordinal(m)``.

(P) results expose **outcome fields only**; match statistics require (K).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import polars as pl

OUTCOME_FIELDS = (
    "match_key",
    "tournament_id",
    "round",
    "round_ordinal",
    "player_a_id",
    "player_b_id",
    "winner_id",
    "a_won",
    "outcome_type",
    "score_raw",
    "score_status",
    "sets_a",
    "sets_b",
    "games_a",
    "games_b",
    "knowable_at_utc",
)


@dataclass(frozen=True)
class Result:
    match_key: str
    tournament_id: str
    round_ordinal: int
    player_a_id: str
    player_b_id: str
    winner_id: str
    knowable_at_utc: datetime
    precedence_ok: bool

    @property
    def players(self) -> tuple[str, str]:
        return (self.player_a_id, self.player_b_id)


@dataclass(frozen=True)
class Target:
    match_key: str
    tournament_id: str
    round_ordinal: int
    player_a_id: str
    player_b_id: str
    prediction_cutoff_utc: datetime
    precedence_ok: bool

    @property
    def players(self) -> tuple[str, str]:
        return (self.player_a_id, self.player_b_id)


def _precedence_ok() -> pl.Expr:
    return pl.col("precedence_eligible") & ~pl.col("precedence_conflict")


def admissible_results(matches: pl.DataFrame, target_key: str) -> pl.DataFrame:
    """Results admissible for ``target_key`` with an ``admitted_via`` column (``K`` or ``P``).

    Rows admitted only through (P) carry outcome fields only.
    """
    target = matches.filter(pl.col("match_key") == target_key)
    if target.height != 1:
        raise KeyError(f"unknown match {target_key}")
    t = target.row(0, named=True)
    others = matches.filter(pl.col("match_key") != target_key)
    via_k = pl.col("knowable_at_utc") <= t["prediction_cutoff_utc"]
    target_precedence = bool(t["precedence_eligible"]) and not bool(t["precedence_conflict"])
    via_p = (
        pl.lit(target_precedence)
        & _precedence_ok()
        & (pl.col("tournament_id") == t["tournament_id"])
        & (pl.col("round_ordinal") < t["round_ordinal"])
        & (
            pl.col("player_a_id").is_in([t["player_a_id"], t["player_b_id"]])
            | pl.col("player_b_id").is_in([t["player_a_id"], t["player_b_id"]])
        )
    )
    k_rows = others.filter(via_k).with_columns(pl.lit("K").alias("admitted_via"))
    p_rows = (
        others.filter(~via_k & via_p)
        .select(OUTCOME_FIELDS)
        .with_columns(pl.lit("P").alias("admitted_via"))
    )
    return pl.concat([k_rows, p_rows], how="diagonal")


def _results(matches: pl.DataFrame) -> list[Result]:
    frame = matches.select(
        "match_key", "tournament_id", "round_ordinal", "player_a_id", "player_b_id",
        "winner_id", "knowable_at_utc", _precedence_ok().alias("precedence_ok"),
    )  # fmt: skip
    return [Result(*row) for row in frame.iter_rows()]


def _targets(matches: pl.DataFrame) -> list[Target]:
    frame = matches.select(
        "match_key", "tournament_id", "round_ordinal", "player_a_id", "player_b_id",
        "prediction_cutoff_utc", _precedence_ok().alias("precedence_ok"),
    )  # fmt: skip
    return [Target(*row) for row in frame.iter_rows()]


def replay[S](
    matches: pl.DataFrame,
    *,
    init: Callable[[], S],
    apply_batch: Callable[[S, Sequence[Result]], S],
    copy_state: Callable[[S], S],
    read: Callable[[S, Target], Mapping[str, Any]],
) -> pl.DataFrame:
    """Compute point-in-time features for every match.

    - Global state absorbs results in batches of equal ``knowable_at_utc``; ``apply_batch`` must
      compute every update in a batch from the pre-batch state (order independence).
    - Before reading a target, global state contains exactly the (K) results for its cutoff.
    - Participants' (P) results not yet in global state are applied to a *copy* (the overlay),
      grouped by round ordinal; the overlay never mutates global state.
    - Each target is read before any batch containing it is applied (predict-before-update).
    """
    results = sorted(_results(matches), key=lambda r: (r.knowable_at_utc, r.match_key))
    batches: list[list[Result]] = []
    for r in results:
        if batches and batches[-1][0].knowable_at_utc == r.knowable_at_utc:
            batches[-1].append(r)
        else:
            batches.append([r])

    own_path: dict[tuple[str, str], list[Result]] = defaultdict(list)
    for r in results:
        if r.precedence_ok:
            for p in r.players:
                own_path[(r.tournament_id, p)].append(r)

    state = init()
    applied = 0
    rows: list[dict[str, Any]] = []
    for target in sorted(_targets(matches), key=lambda t: (t.prediction_cutoff_utc, t.match_key)):
        while (
            applied < len(batches)
            and batches[applied][0].knowable_at_utc <= target.prediction_cutoff_utc
        ):
            state = apply_batch(state, batches[applied])
            applied += 1
        view = state
        if target.precedence_ok:
            pending = {
                r.match_key: r
                for p in target.players
                for r in own_path[(target.tournament_id, p)]
                if r.round_ordinal < target.round_ordinal
                and r.knowable_at_utc > target.prediction_cutoff_utc
            }
            if pending:
                view = copy_state(state)
                by_round: dict[int, list[Result]] = defaultdict(list)
                for r in pending.values():
                    by_round[r.round_ordinal].append(r)
                for ordinal in sorted(by_round):
                    view = apply_batch(view, sorted(by_round[ordinal], key=lambda r: r.match_key))
        rows.append({"match_key": target.match_key, **read(view, target)})
    return pl.DataFrame(rows)


def rankings_as_of(rankings: pl.DataFrame, cutoff: datetime) -> pl.DataFrame:
    """Latest ranking per player whose ``knowable_at_utc`` is at or before ``cutoff``."""
    return (
        rankings.filter(pl.col("knowable_at_utc") <= cutoff)
        .sort("player_id", "ranking_date")
        .group_by("player_id", maintain_order=True)
        .last()
    )
