"""Gate 0: deterministic anomaly classification and modelling eligibility (docs/reviews/phase-2.md).

Every canonical match receives flags, a primary reason, and three decisions:

- ``elo_update_baseline``: the result may update ratings under the baseline policy;
- ``elo_update_retirements``: the same, but retirement-like results are also used (sensitivity);
- ``eval_eligible``: the match may be scored as a prediction target.

Records involved in unresolved contradictions are excluded from training and evaluation but keep
their provenance and reason. Results whose round label is doubtful update ratings only once the
whole tournament is certainly over (deferred knowability) and get no earlier-round precedence.

Policies are pure functions of the canonical tables plus two curated reference files, so they are
deterministic and independent of row order.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import cache
from importlib import resources

import polars as pl

from quantedge.canonical.score import parse_score
from quantedge.core.chronology import latest_instant_expr
from quantedge.core.hashing import sha256_fields

POLICY_VERSION = "eligibility.v1"

# Exclusion reasons in priority order (the first applicable becomes ``primary_reason``).
EXCLUDE_REASONS = (
    "PLACEHOLDER_PLAYER",
    "CONTRADICTORY_DUPLICATE",
    "DUPLICATE_RECORD",
    "KNOCKOUT_CONTRADICTION",
    "SCORE_CONTRADICTION",
    "WALKOVER",
    "DEFAULT_UNPLAYED",
    "ABANDONED",
    "SCORE_MISSING",
    "SCORE_UNCERTAIN",
    "SCORE_MALFORMED",
)
RETIREMENT_LIKE = ("RETIRED", "DEFAULT_PLAYED", "INCOMPLETE_UNTERMINATED")
INCLUDED_WITH_FLAG = ("ROUND_LABEL_CONFLICT", "SCORE_FORMAT_MISMATCH")
ALL_REASONS = (*EXCLUDE_REASONS, *RETIREMENT_LIKE, *INCLUDED_WITH_FLAG, "OK")


@dataclass(frozen=True)
class CurationReference:
    placeholder_ids: frozenset[str]
    team_levels: frozenset[str]
    team_name_patterns: tuple[str, ...]
    fingerprint: str


@cache
def load_curation_reference() -> CurationReference:
    package = resources.files("quantedge.reference")
    placeholder_text = package.joinpath("placeholder_players.csv").read_text(encoding="utf-8")
    team_text = package.joinpath("team_events.csv").read_text(encoding="utf-8")
    placeholders = frozenset(r["player_id"] for r in csv.DictReader(placeholder_text.splitlines()))
    team_rows = list(csv.DictReader(team_text.splitlines()))
    return CurationReference(
        placeholder_ids=placeholders,
        team_levels=frozenset(r["value"] for r in team_rows if r["rule"] == "level"),
        team_name_patterns=tuple(r["value"] for r in team_rows if r["rule"] == "name_regex"),
        fingerprint=sha256_fields(POLICY_VERSION, placeholder_text, team_text),
    )


def score_subclass(score: str | None, best_of: int | None) -> str:
    """Refine a Phase 1 ``inconsistent`` score into an auditable subclass."""
    parsed = parse_score(score, best_of)
    if parsed.score_status != "inconsistent" or parsed.outcome_type == "walkover":
        return "not_inconsistent"
    complete = [s.is_complete(short_format=parsed.short_format) for s in parsed.sets]
    won, lost = parsed.sets_won_by_winner, parsed.sets_won_by_loser
    if not all(complete[:-1]):
        return "incomplete_nonfinal_set"
    if complete and not complete[-1]:
        return "incomplete_final_set_no_terminal"
    if won < lost:
        return "winner_fewer_sets"
    if won == lost:
        return "sets_tied"
    return "complete_winner_majority_bestof_mismatch"


SUBCLASS_REASON = {
    "complete_winner_majority_bestof_mismatch": "SCORE_FORMAT_MISMATCH",
    "incomplete_final_set_no_terminal": "INCOMPLETE_UNTERMINATED",
    "incomplete_nonfinal_set": "INCOMPLETE_UNTERMINATED",
    "winner_fewer_sets": "SCORE_CONTRADICTION",
    "sets_tied": "SCORE_CONTRADICTION",
}


def _is_team_event(tournaments: pl.DataFrame, ref: CurationReference) -> pl.DataFrame:
    name_match = pl.lit(False)
    for pattern in ref.team_name_patterns:
        name_match = name_match | pl.col("name").str.contains(pattern).fill_null(False)
    return tournaments.select(
        "tournament_id",
        (pl.col("level").is_in(list(ref.team_levels)).fill_null(False) | name_match).alias(
            "team_event"
        ),
    )


def _sides(frame: pl.DataFrame, *extra: str) -> pl.DataFrame:
    return pl.concat(
        [
            frame.select("match_key", *extra, pl.col(c).alias("pid"))
            for c in ("player_a_id", "player_b_id")
        ]
    )


def build_eligibility(matches: pl.DataFrame, tournaments: pl.DataFrame) -> pl.DataFrame:
    """Return one row per match with flags, primary reason and eligibility decisions."""
    ref = load_curation_reference()
    frame = matches.join(
        tournaments.select("tournament_id", "surface", "name", "level"),
        on="tournament_id",
        how="left",
    ).join(_is_team_event(tournaments, ref), on="tournament_id", how="left")

    # ---- row-level flags
    subclasses = {
        (s, b): score_subclass(s, b)
        for s, b in frame.filter(pl.col("score_status") == "inconsistent")
        .select("score_raw", "best_of")
        .unique()
        .iter_rows()
    }
    sub = [
        subclasses.get((s, b), "not_inconsistent") if st == "inconsistent" else "not_inconsistent"
        for s, b, st in frame.select("score_raw", "best_of", "score_status").iter_rows()
    ]
    placeholder = pl.col("player_a_id").is_in(list(ref.placeholder_ids)) | pl.col(
        "player_b_id"
    ).is_in(list(ref.placeholder_ids))
    played_default = (pl.col("outcome_type") == "default") & (
        (pl.col("n_sets") > 0) | ((pl.col("games_a") + pl.col("games_b")) > 0)
    )
    frame = frame.with_columns(
        pl.Series("score_subclass", sub, dtype=pl.String),
        placeholder.alias("placeholder_player"),
        played_default.alias("default_played"),
    )

    # ---- structural pass over plausible draw rows
    structural = frame.filter(
        ~pl.col("placeholder_player") & (pl.col("score_status") != "missing")
    ).with_columns(
        pl.min_horizontal("player_a_id", "player_b_id").alias("_lo"),
        pl.max_horizontal("player_a_id", "player_b_id").alias("_hi"),
    )
    knockout = structural.filter(pl.col("round") != "RR")
    pair_groups = (
        knockout.group_by("tournament_id", "round", "_lo", "_hi")
        .agg(pl.len().alias("_n"), pl.col("winner_id").n_unique().alias("_winners"))
        .filter(pl.col("_n") > 1)
    )
    in_pairs = knockout.join(pair_groups, on=["tournament_id", "round", "_lo", "_hi"])
    contradictory = set(in_pairs.filter(pl.col("_winners") > 1)["match_key"].to_list())
    same_winner = (
        in_pairs.filter(pl.col("_winners") == 1)
        .with_columns((pl.col("score_status") != "ok").alias("_worse"))
        .sort("tournament_id", "round", "_lo", "_hi", "_worse", "match_key")
    )
    keep = same_winner.group_by("tournament_id", "round", "_lo", "_hi", maintain_order=True).first()
    duplicates = set(same_winner["match_key"].to_list()) - set(keep["match_key"].to_list())

    removed = contradictory | duplicates
    remaining = knockout.filter(~pl.col("match_key").is_in(list(removed)))
    side_rows = _sides(remaining.filter(pl.col("precedence_eligible")), "tournament_id", "round")
    conflict_groups = (
        side_rows.group_by("tournament_id", "round", "pid")
        .agg(pl.col("match_key"), pl.len().alias("_n"))
        .filter(pl.col("_n") > 1)
    )
    round_conflicts = set(conflict_groups.explode("match_key")["match_key"].to_list())

    # Knockout logic: a main-draw loser must not appear in a later main-draw round (individual
    # events only; the SF loser may play a bronze match).
    main = remaining.filter(
        pl.col("draw_category").str.ends_with("_main")
        & pl.col("precedence_eligible")
        & ~pl.col("team_event").fill_null(False)
        & ~pl.col("match_key").is_in(list(round_conflicts))
    )
    losers = main.select(
        "tournament_id",
        pl.col("match_key").alias("loss_key"),
        pl.when(pl.col("a_won"))
        .then(pl.col("player_b_id"))
        .otherwise(pl.col("player_a_id"))
        .alias("pid"),
        pl.col("round_ordinal").alias("lost_at"),
        pl.col("round").alias("lost_round"),
    )
    later = losers.join(
        _sides(main, "tournament_id", "round", "round_ordinal"), on=["tournament_id", "pid"]
    ).filter(
        (pl.col("round_ordinal") > pl.col("lost_at"))
        & ~((pl.col("lost_round") == "SF") & (pl.col("round") == "BR"))
    )
    knockout_contradictions = set(later["loss_key"].to_list()) | set(later["match_key"].to_list())

    frame = frame.with_columns(
        pl.col("match_key").is_in(list(contradictory)).alias("contradictory_duplicate"),
        pl.col("match_key").is_in(list(duplicates)).alias("duplicate_record"),
        pl.col("match_key").is_in(list(round_conflicts)).alias("round_label_conflict"),
        pl.col("match_key").is_in(list(knockout_contradictions)).alias("knockout_contradiction"),
        pl.col("team_event").fill_null(False),
    )

    # ---- primary reason (priority order)
    sub_reason = pl.col("score_subclass").replace_strict(
        SUBCLASS_REASON, default=None, return_dtype=pl.String
    )
    outcome = pl.col("outcome_type")
    status = pl.col("score_status")
    reason = (
        pl.when(pl.col("placeholder_player"))
        .then(pl.lit("PLACEHOLDER_PLAYER"))
        .when(pl.col("contradictory_duplicate"))
        .then(pl.lit("CONTRADICTORY_DUPLICATE"))
        .when(pl.col("duplicate_record"))
        .then(pl.lit("DUPLICATE_RECORD"))
        .when(pl.col("knockout_contradiction"))
        .then(pl.lit("KNOCKOUT_CONTRADICTION"))
        .when(sub_reason == "SCORE_CONTRADICTION")
        .then(pl.lit("SCORE_CONTRADICTION"))
        .when(outcome == "walkover")
        .then(pl.lit("WALKOVER"))
        .when((outcome == "default") & ~pl.col("default_played"))
        .then(pl.lit("DEFAULT_UNPLAYED"))
        .when(outcome == "abandoned")
        .then(pl.lit("ABANDONED"))
        .when(status == "missing")
        .then(pl.lit("SCORE_MISSING"))
        .when(status == "uncertain")
        .then(pl.lit("SCORE_UNCERTAIN"))
        .when(status == "malformed")
        .then(pl.lit("SCORE_MALFORMED"))
        .when(outcome == "retired")
        .then(pl.lit("RETIRED"))
        .when(pl.col("default_played"))
        .then(pl.lit("DEFAULT_PLAYED"))
        .when(sub_reason == "INCOMPLETE_UNTERMINATED")
        .then(pl.lit("INCOMPLETE_UNTERMINATED"))
        .when(pl.col("round_label_conflict"))
        .then(pl.lit("ROUND_LABEL_CONFLICT"))
        .when(sub_reason == "SCORE_FORMAT_MISMATCH")
        .then(pl.lit("SCORE_FORMAT_MISMATCH"))
        .otherwise(pl.lit("OK"))
    )
    frame = frame.with_columns(reason.alias("primary_reason"))
    usable = pl.col("primary_reason").is_in(["OK", *INCLUDED_WITH_FLAG])
    defer = pl.col("round_label_conflict")
    frame = frame.with_columns(
        usable.alias("elo_update_baseline"),
        (usable | pl.col("primary_reason").is_in(list(RETIREMENT_LIKE))).alias(
            "elo_update_retirements"
        ),
        pl.col("primary_reason").is_in(["OK", "SCORE_FORMAT_MISMATCH"]).alias("eval_eligible"),
        defer.alias("knowability_deferred"),
        pl.when(defer)
        .then(latest_instant_expr("tournament_end_bound"))
        .otherwise(pl.col("knowable_at_utc"))
        .alias("effective_knowable_at_utc"),
        (pl.col("precedence_eligible") & ~defer).alias("precedence_allowed"),
        pl.lit(POLICY_VERSION).alias("policy_version"),
    )
    return frame.select(ELIGIBILITY_COLUMNS).sort("match_key")


ELIGIBILITY_COLUMNS = [
    "match_key",
    "surface",
    "team_event",
    "placeholder_player",
    "contradictory_duplicate",
    "duplicate_record",
    "round_label_conflict",
    "knockout_contradiction",
    "default_played",
    "score_subclass",
    "primary_reason",
    "elo_update_baseline",
    "elo_update_retirements",
    "eval_eligible",
    "knowability_deferred",
    "effective_knowable_at_utc",
    "precedence_allowed",
    "policy_version",
]


def suspicious_identities(players: pl.DataFrame, used_ids: pl.Series) -> pl.DataFrame:
    """Rule-based placeholder candidates (for review); the curated CSV is authoritative."""
    rule = (
        (pl.col("name_last").str.to_lowercase() == "unknown")
        & (
            pl.col("name_first").is_null()
            | pl.col("name_first").str.to_lowercase().is_in(["u", "x", "unknown"])
        )
    ) | pl.col("name_last").str.contains(r"^(?i)bye\d*$").fill_null(False)
    return players.filter(rule & pl.col("player_id").is_in(used_ids.unique().to_list())).select(
        "player_id", "name_first", "name_last"
    )
