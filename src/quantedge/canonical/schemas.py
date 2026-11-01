"""Canonical table schemas (single source of truth for columns, dtypes and tags).

Column tags:
- ``post_match``: known only after the match (outcome, score, duration, statistics). Such
  columns are forbidden as inputs for the same match's prediction (ARCHITECTURE.md §5.7).
- ``locator``: a position in a source file; excluded from canonical content hashes because it is
  not content.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandera.polars as pa
import polars as pl

from quantedge.canonical.reference import OUTCOME_TYPES
from quantedge.canonical.score import SCORE_STATUSES

UTC_DT = pl.Datetime("us", "UTC")
PLAYER_ID = r"^[a-z][a-z0-9_]*:[0-9]+$"
TOURNAMENT_ID = r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9_\-./]*$"
MATCH_KEY = r"^m1_[0-9a-f]{32}$"
SHA256 = r"^[0-9a-f]{64}$"

REASON_CODES = (
    "INVALID_TOURNEY_ID",
    "INVALID_TOURNEY_DATE",
    "INVALID_PLAYER_ID",
    "SAME_PLAYER",
    "UNKNOWN_ROUND",
    "DUPLICATE_ROW",
    "KEY_COLLISION",
    "INVALID_RANKING_DATE",
    "INVALID_RANK",
    "RANKING_CONFLICT",
    "PLAYER_CONFLICT",
)


@dataclass(frozen=True)
class Col:
    name: str
    dtype: pl.DataType
    nullable: bool = False
    unique: bool = False
    checks: tuple[pa.Check, ...] = ()
    post_match: bool = False
    locator: bool = False


def _isin(values: tuple[str, ...]) -> tuple[pa.Check, ...]:
    return (pa.Check.isin(list(values)),)


def _regex(pattern: str) -> tuple[pa.Check, ...]:
    return (pa.Check.str_matches(pattern),)


_COMMON_PROVENANCE = (
    Col("licence_class", pl.String(), checks=_isin(("RESEARCH_NC",))),
    Col("source", pl.String()),
    Col("source_file", pl.String()),
    Col("source_row_sha256", pl.String(), checks=_regex(SHA256)),
)

TABLES: dict[str, tuple[Col, ...]] = {
    "players": (
        Col("player_id", pl.String(), unique=True, checks=_regex(PLAYER_ID)),
        Col("source_player_id", pl.Int64()),
        Col("name_first", pl.String(), nullable=True),
        Col("name_last", pl.String(), nullable=True),
        Col("hand", pl.String(), checks=_isin(("R", "L", "A", "U"))),
        Col("birth_date", pl.Date(), nullable=True),
        Col("birth_year", pl.Int16(), nullable=True),
        Col("birth_date_precision", pl.String(), checks=_isin(("day", "year", "unknown"))),
        Col("country_ioc", pl.String(), nullable=True),
        Col("height_cm", pl.Int16(), nullable=True, checks=(pa.Check.in_range(100, 250),)),
        Col("wikidata_id", pl.String(), nullable=True),
        *_COMMON_PROVENANCE,
    ),
    "tournaments": (
        Col("tournament_id", pl.String(), unique=True, checks=_regex(TOURNAMENT_ID)),
        Col("source_tourney_id", pl.String()),
        Col("season", pl.Int16()),
        Col("name", pl.String(), nullable=True),
        Col("level", pl.String(), nullable=True),
        Col("tier", pl.String(), checks=_isin(("tour", "challenger", "itf", "other"))),
        Col("surface", pl.String(), nullable=True),
        Col("indoor", pl.Boolean(), nullable=True),
        Col("draw_size", pl.Int32(), nullable=True),
        Col("start_date", pl.Date()),
        Col("event_class", pl.String()),
        Col("nominal_days", pl.Int16()),
        Col("end_date_bound", pl.Date()),
        Col("attribute_conflicts", pl.Int16(), checks=(pa.Check.ge(0),)),
        Col("licence_class", pl.String(), checks=_isin(("RESEARCH_NC",))),
        Col("source", pl.String()),
    ),
    "matches": (
        Col("match_key", pl.String(), unique=True, checks=_regex(MATCH_KEY)),
        Col("key_resolution", pl.String(), checks=_isin(("base", "match_num_discriminator"))),
        Col("tournament_id", pl.String(), checks=_regex(TOURNAMENT_ID)),
        Col("season", pl.Int16()),
        Col("draw_category", pl.String()),
        Col("round", pl.String()),
        Col("round_ordinal", pl.Int16()),
        Col("precedence_eligible", pl.Boolean()),
        Col("precedence_conflict", pl.Boolean()),
        Col("best_of", pl.Int8(), nullable=True),
        Col("player_a_id", pl.String(), checks=_regex(PLAYER_ID)),
        Col("player_b_id", pl.String(), checks=_regex(PLAYER_ID)),
        Col("source_match_num", pl.Int32(), nullable=True),
        Col("event_class", pl.String()),
        Col("tournament_start_date", pl.Date()),
        Col("tournament_end_bound", pl.Date()),
        Col("match_day_lo", pl.Date()),
        Col("match_day_hi", pl.Date()),
        Col("match_date", pl.Date(), nullable=True),
        Col("scheduled_start_utc", UTC_DT, nullable=True),
        Col("actual_start_utc", UTC_DT, nullable=True),
        Col("completed_at_utc", UTC_DT, nullable=True),
        Col("published_at_utc", UTC_DT, nullable=True),
        Col("prediction_cutoff_utc", UTC_DT),
        Col("knowable_at_utc", UTC_DT),
        Col("knowability_rule", pl.String(), checks=_isin(("day_bounds",))),
        Col("winner_id", pl.String(), checks=_regex(PLAYER_ID), post_match=True),
        Col("a_won", pl.Boolean(), post_match=True),
        Col("outcome_type", pl.String(), checks=_isin(OUTCOME_TYPES), post_match=True),
        Col("score_raw", pl.String(), nullable=True, post_match=True),
        Col("score_status", pl.String(), checks=_isin(SCORE_STATUSES), post_match=True),
        Col("n_sets", pl.Int8(), post_match=True),
        Col("sets_a", pl.Int8(), post_match=True),
        Col("sets_b", pl.Int8(), post_match=True),
        Col("games_a", pl.Int16(), post_match=True),
        Col("games_b", pl.Int16(), post_match=True),
        Col("tiebreak_sets", pl.Int8(), post_match=True),
        Col("has_match_tiebreak", pl.Boolean(), post_match=True),
        Col("minutes", pl.Int32(), nullable=True, post_match=True),
        *_COMMON_PROVENANCE,
    ),
    "match_entry": (
        Col("match_key", pl.String(), checks=_regex(MATCH_KEY)),
        Col("player_id", pl.String(), checks=_regex(PLAYER_ID)),
        Col("side", pl.String(), checks=_isin(("A", "B"))),
        Col("seed", pl.Int32(), nullable=True),
        Col("entry", pl.String(), nullable=True),
        Col("rank_at_event_start", pl.Int32(), nullable=True),
        Col("rank_points_at_event_start", pl.Int32(), nullable=True),
        Col("age_at_event_start", pl.Float64(), nullable=True),
        Col("height_cm_reported", pl.Int16(), nullable=True),
        Col("hand_reported", pl.String(), nullable=True),
        Col("country_reported", pl.String(), nullable=True),
        Col("name_reported", pl.String(), nullable=True),
    ),
    "match_stats": (
        Col("match_key", pl.String(), checks=_regex(MATCH_KEY)),
        Col("player_id", pl.String(), checks=_regex(PLAYER_ID)),
        Col("side", pl.String(), checks=_isin(("A", "B"))),
        Col("aces", pl.Int16(), nullable=True, post_match=True),
        Col("double_faults", pl.Int16(), nullable=True, post_match=True),
        Col("serve_points", pl.Int16(), nullable=True, post_match=True),
        Col("first_serves_in", pl.Int16(), nullable=True, post_match=True),
        Col("first_serve_points_won", pl.Int16(), nullable=True, post_match=True),
        Col("second_serve_points_won", pl.Int16(), nullable=True, post_match=True),
        Col("service_games", pl.Int16(), nullable=True, post_match=True),
        Col("break_points_saved", pl.Int16(), nullable=True, post_match=True),
        Col("break_points_faced", pl.Int16(), nullable=True, post_match=True),
        Col("return_points_played", pl.Int16(), nullable=True, post_match=True),
        Col("return_points_won", pl.Int16(), nullable=True, post_match=True),
        Col("knowable_at_utc", UTC_DT),
    ),
    "rankings": (
        Col("player_id", pl.String(), checks=_regex(PLAYER_ID)),
        Col("ranking_date", pl.Date()),
        Col("rank", pl.Int32(), checks=(pa.Check.ge(1),)),
        Col("points", pl.Int32(), nullable=True),
        Col("knowable_at_utc", UTC_DT),
        *_COMMON_PROVENANCE,
    ),
    "rejected_rows": (
        Col("source_file", pl.String()),
        Col("source_row_number", pl.Int64(), locator=True),
        Col("source_row_sha256", pl.String(), checks=_regex(SHA256)),
        Col("reason_code", pl.String(), checks=_isin(REASON_CODES)),
        Col("detail", pl.String(), nullable=True),
    ),
}

PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "players": ("player_id",),
    "tournaments": ("tournament_id",),
    "matches": ("match_key",),
    "match_entry": ("match_key", "player_id"),
    "match_stats": ("match_key", "player_id"),
    "rankings": ("player_id", "ranking_date"),
    "rejected_rows": ("source_file", "source_row_number"),
}

FORBIDDEN_COLUMN_PREFIXES = ("winner_", "loser_", "w_", "l_")
ALLOWED_OUTCOME_COLUMNS = frozenset({"winner_id"})


def column_names(table: str) -> list[str]:
    return [c.name for c in TABLES[table]]


def post_match_columns(table: str) -> frozenset[str]:
    return frozenset(c.name for c in TABLES[table] if c.post_match)


def content_hash_excludes(table: str) -> tuple[str, ...]:
    return tuple(c.name for c in TABLES[table] if c.locator)


def dtypes(table: str) -> dict[str, pl.DataType]:
    return {c.name: c.dtype for c in TABLES[table]}


def pandera_schema(table: str) -> pa.DataFrameSchema:
    columns = {
        c.name: pa.Column(c.dtype, checks=list(c.checks), nullable=c.nullable, unique=c.unique)
        for c in TABLES[table]
    }
    return pa.DataFrameSchema(columns, strict=True, unique=list(PRIMARY_KEYS[table]))


def validate_table(table: str, frame: pl.DataFrame) -> pl.DataFrame:
    """Validate against the Pandera schema; raises ``pandera.errors.SchemaErrors`` lazily."""
    validated: pl.DataFrame = pandera_schema(table).validate(frame, lazy=True)
    return validated
