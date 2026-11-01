"""Raw Sackmann snapshot -> validated canonical Parquet tables.

Guarantees (tested):
- every raw row is either emitted into exactly one canonical table or recorded in ``rejected_rows``;
- identifiers and A/B orientation never depend on the winner, row order, or ``match_num``
  (except as the documented collision discriminator);
- canonical content hashes are identical across rebuilds and input row order.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from quantedge import __version__
from quantedge.canonical import schemas
from quantedge.canonical.reference import Reference, load_reference
from quantedge.canonical.score import ParsedScore, parse_score
from quantedge.core.chronology import (
    ChronologyConfig,
    earliest_instant_expr,
    latest_instant_expr,
)
from quantedge.core.hashing import frame_content_hash, row_hashes, sha256_fields
from quantedge.core.ids import (
    SACKMANN_NS,
    discriminated_match_key,
    match_key,
    orient,
)
from quantedge.core.lineage import LicenceClass
from quantedge.core.manifest import (
    BUILD_MANIFEST_NAME,
    BuildManifest,
    RawManifest,
    TableEntry,
    write_json,
)

SOURCE = "sackmann_archive"
LATEST_POINTER = "LATEST"

MATCH_FILE = re.compile(r"^atp/atp_matches_(?:(qual_chall)_)?(\d{4})\.csv$")
PLAYERS_FILE = "atp/atp_players.csv"
RANKINGS_FILE = re.compile(r"^atp/atp_rankings_[0-9a-z]+\.csv$")
FILE_PRIORITY = {"tour": 0, "qual_chall": 1}

RAW_MATCH_COLUMNS = (
    "tourney_id", "tourney_name", "surface", "draw_size", "tourney_level", "tourney_date",
    "match_num", "winner_id", "winner_seed", "winner_entry", "winner_name", "winner_hand",
    "winner_ht", "winner_ioc", "winner_age", "loser_id", "loser_seed", "loser_entry",
    "loser_name", "loser_hand", "loser_ht", "loser_ioc", "loser_age", "score", "best_of",
    "round", "minutes", "w_ace", "w_df", "w_svpt", "w_1stIn", "w_1stWon", "w_2ndWon",
    "w_SvGms", "w_bpSaved", "w_bpFaced", "l_ace", "l_df", "l_svpt", "l_1stIn", "l_1stWon",
    "l_2ndWon", "l_SvGms", "l_bpSaved", "l_bpFaced", "winner_rank", "winner_rank_points",
    "loser_rank", "loser_rank_points",
)  # fmt: skip
RAW_PLAYER_COLUMNS = (
    "player_id", "name_first", "name_last", "hand", "dob", "ioc", "height", "wikidata_id",
)  # fmt: skip
RAW_RANKING_COLUMNS = ("ranking_date", "rank", "player", "points")

# Per-side raw columns -> canonical names (side-neutral).
_ENTRY_FIELDS = {
    "seed": "seed", "entry": "entry", "rank": "rank_at_event_start",
    "rank_points": "rank_points_at_event_start", "age": "age_at_event_start",
    "ht": "height_cm_reported", "hand": "hand_reported", "ioc": "country_reported",
    "name": "name_reported",
}  # fmt: skip
_STAT_FIELDS = {
    "ace": "aces", "df": "double_faults", "svpt": "serve_points", "1stIn": "first_serves_in",
    "1stWon": "first_serve_points_won", "2ndWon": "second_serve_points_won",
    "SvGms": "service_games", "bpSaved": "break_points_saved", "bpFaced": "break_points_faced",
}  # fmt: skip

_INT = r"^\s*[0-9]+\s*$"
_TOURNEY_ID = r"^[A-Za-z0-9][A-Za-z0-9_\-./]*$"


class SchemaDriftError(ValueError):
    """Raised when a raw file's columns differ from the expected layout."""


class ReconciliationError(AssertionError):
    """Raised when accepted + rejected rows do not account for every input row."""


@dataclass(frozen=True)
class BuildResult:
    build_id: str
    out_dir: Path
    tables: dict[str, pl.DataFrame]
    manifest: BuildManifest


# --------------------------------------------------------------------------- reading


def read_raw_csv(path: Path, expected: tuple[str, ...], rel: str) -> pl.DataFrame:
    frame = pl.read_csv(path, infer_schema=False)
    if tuple(frame.columns) != expected:
        raise SchemaDriftError(f"{rel}: columns {frame.columns} != expected layout")
    return frame.with_columns(
        pl.lit(rel).alias("source_file"),
        pl.int_range(1, pl.len() + 1, dtype=pl.Int64).alias("source_row_number"),
    )


def _strip(col: str) -> pl.Expr:
    return pl.col(col).str.strip_chars()


def _int(col: str, dtype: pl.DataType) -> pl.Expr:
    """Strictly parse a non-negative integer string; anything else becomes null."""
    stripped = _strip(col)
    return (
        pl.when(stripped.str.contains(r"^[0-9]+$"))
        .then(stripped.cast(pl.Int64, strict=False))
        .otherwise(None)
        .cast(dtype, strict=False)
    )


def _canonical_player(col: str) -> pl.Expr:
    return pl.lit(f"{SACKMANN_NS}:") + _strip(col).cast(pl.Int64).cast(pl.String)


def _rejections(frame: pl.DataFrame, reason: pl.Expr | str, detail: pl.Expr | None) -> pl.DataFrame:
    reason_expr = pl.lit(reason) if isinstance(reason, str) else reason
    return frame.select(
        pl.col("source_file"),
        pl.col("source_row_number"),
        pl.col("source_row_sha256"),
        reason_expr.alias("reason_code"),
        (detail if detail is not None else pl.lit(None, dtype=pl.String)).alias("detail"),
    )


# --------------------------------------------------------------------------- matches


def _read_matches(snapshot: Path, manifest: RawManifest) -> pl.DataFrame:
    frames = []
    for entry in manifest.files:
        m = MATCH_FILE.match(entry.path)
        if not m:
            continue
        category = "qual_chall" if m.group(1) else "tour"
        frame = read_raw_csv(snapshot / entry.path, RAW_MATCH_COLUMNS, entry.path)
        frames.append(
            frame.with_columns(
                pl.lit(category).alias("file_category"),
                pl.lit(FILE_PRIORITY[category], dtype=pl.Int8).alias("file_priority"),
                pl.lit(int(m.group(2)), dtype=pl.Int16).alias("season"),
            )
        )
    if not frames:
        raise SchemaDriftError("snapshot contains no match files")
    raw = pl.concat(frames, how="vertical")
    return raw.with_columns(row_hashes(raw, RAW_MATCH_COLUMNS, "qe.source_row.v1"))


def _keep_first(frame: pl.DataFrame, group: str | list[str], order: list[str]) -> pl.DataFrame:
    """Add ``_dup`` = 1-based position within ``group`` under a deterministic total ``order``."""
    return frame.sort(order).with_columns(
        pl.int_range(1, pl.len() + 1, dtype=pl.Int64).over(group).alias("_dup")
    )


def _first_failure(checks: Iterable[tuple[pl.Expr, str]]) -> pl.Expr:
    expr: Any = None
    for condition, code in checks:
        expr = (pl.when(condition) if expr is None else expr.when(condition)).then(pl.lit(code))
    assert expr is not None
    result: pl.Expr = expr.otherwise(pl.lit(None, dtype=pl.String))
    return result


def _tournament_table(rows: pl.DataFrame, ref: Reference) -> pl.DataFrame:
    attrs = ["tourney_name", "tourney_level", "surface", "draw_size_int", "start_date", "season"]
    grouped = rows.group_by("tournament_id").agg(
        pl.col("tourney_id_clean").min().alias("source_tourney_id"),
        pl.col("season").min(),
        pl.col("tourney_name").min().alias("name"),
        pl.col("tourney_level").min().alias("level"),
        pl.col("surface").min(),
        pl.col("draw_size_int").min().alias("draw_size"),
        pl.col("start_date").min(),
        pl.sum_horizontal(
            [(pl.col(a).drop_nulls().n_unique() > 1).cast(pl.Int16) for a in attrs]
        ).alias("attribute_conflicts"),
    )
    classes = [
        ref.event_class(level, draw)
        for level, draw in grouped.select("level", "draw_size").iter_rows()
    ]
    tiers = [ref.tier(level) for level in grouped["level"].to_list()]
    return grouped.with_columns(
        pl.Series("tier", tiers, dtype=pl.String),
        pl.Series("event_class", [c.event_class for c in classes], dtype=pl.String),
        pl.Series("nominal_days", [c.nominal_days for c in classes], dtype=pl.Int16),
        pl.Series("end_offset_days", [c.end_offset_days for c in classes], dtype=pl.Int32),
        pl.lit(None, dtype=pl.Boolean).alias("indoor"),
        pl.lit(LicenceClass.RESEARCH_NC.value).alias("licence_class"),
        pl.lit(SOURCE).alias("source"),
    ).with_columns(
        (pl.col("start_date") + pl.duration(days=pl.col("end_offset_days")))
        .cast(pl.Date)
        .alias("end_date_bound"),
        pl.col("season").cast(pl.Int16),
        pl.col("draw_size").cast(pl.Int32),
        pl.col("attribute_conflicts").cast(pl.Int16),
    )


def _score_columns(rows: pl.DataFrame) -> pl.DataFrame:
    """Parse each distinct (score, best_of) once and map to A/B-oriented fields."""
    distinct = rows.select("score", "best_of_int").unique()
    parsed: list[ParsedScore] = [
        parse_score(score, best_of) for score, best_of in distinct.iter_rows()
    ]
    lookup = distinct.with_columns(
        pl.Series("outcome_type", [p.outcome_type for p in parsed], dtype=pl.String),
        pl.Series("score_status", [p.score_status for p in parsed], dtype=pl.String),
        pl.Series("n_sets", [len(p.sets) for p in parsed], dtype=pl.Int8),
        pl.Series("sets_w", [p.sets_won_by_winner for p in parsed], dtype=pl.Int8),
        pl.Series("sets_l", [p.sets_won_by_loser for p in parsed], dtype=pl.Int8),
        pl.Series("games_w", [p.games_winner for p in parsed], dtype=pl.Int16),
        pl.Series("games_l", [p.games_loser for p in parsed], dtype=pl.Int16),
        pl.Series("tiebreak_sets", [p.tiebreak_sets for p in parsed], dtype=pl.Int8),
        pl.Series("has_match_tiebreak", [p.has_match_tiebreak for p in parsed], dtype=pl.Boolean),
    )
    joined = rows.join(lookup, on=["score", "best_of_int"], how="left", nulls_equal=True)
    a = pl.col("a_won")
    return joined.with_columns(
        pl.when(a).then(pl.col("sets_w")).otherwise(pl.col("sets_l")).alias("sets_a"),
        pl.when(a).then(pl.col("sets_l")).otherwise(pl.col("sets_w")).alias("sets_b"),
        pl.when(a).then(pl.col("games_w")).otherwise(pl.col("games_l")).alias("games_a"),
        pl.when(a).then(pl.col("games_l")).otherwise(pl.col("games_w")).alias("games_b"),
    )


def build_matches(
    raw: pl.DataFrame, ref: Reference, chrono: ChronologyConfig
) -> tuple[dict[str, pl.DataFrame], pl.DataFrame, dict[str, Any]]:
    """Return canonical match-related tables, rejected rows, and build notes."""
    notes: dict[str, Any] = {}
    staged = raw.with_columns(
        _strip("tourney_id").alias("tourney_id_clean"),
        pl.col("tourney_date").str.strptime(pl.Date, "%Y%m%d", strict=False).alias("start_date"),
        _strip("round").alias("round_clean"),
    )
    reason = _first_failure(
        [
            (
                pl.col("tourney_id_clean").is_null()
                | ~pl.col("tourney_id_clean").str.contains(_TOURNEY_ID),
                "INVALID_TOURNEY_ID",
            ),
            (
                pl.col("start_date").is_null()
                | ~pl.col("tourney_date").str.contains(r"^[0-9]{8}$"),
                "INVALID_TOURNEY_DATE",
            ),
            (
                ~pl.col("winner_id").str.contains(_INT).fill_null(False)
                | ~pl.col("loser_id").str.contains(_INT).fill_null(False),
                "INVALID_PLAYER_ID",
            ),
            (
                _strip("winner_id").cast(pl.Int64, strict=False)
                == _strip("loser_id").cast(pl.Int64, strict=False),
                "SAME_PLAYER",
            ),
            (~pl.col("round_clean").is_in(list(ref.rounds)).fill_null(False), "UNKNOWN_ROUND"),
        ]
    )
    staged = staged.with_columns(reason.alias("_reason"))
    rejected = [
        _rejections(
            staged.filter(pl.col("_reason").is_not_null()),
            pl.col("_reason"),
            pl.concat_str([pl.lit("round="), pl.col("round").fill_null("<null>")], separator=""),
        )
    ]
    ok = staged.filter(pl.col("_reason").is_null()).drop("_reason")

    # Exact duplicates of full source content: keep one deterministically.
    ok = _keep_first(
        ok, "source_row_sha256", ["file_priority", "source_file", "source_row_number"]
    ).rename({"_dup": "_dup_rank"})
    rejected.append(
        _rejections(
            ok.filter(pl.col("_dup_rank") > 1),
            "DUPLICATE_ROW",
            pl.lit("identical source content kept once"),
        )
    )
    ok = ok.filter(pl.col("_dup_rank") == 1).drop("_dup_rank")

    ok = ok.with_columns(
        _canonical_player("winner_id").alias("winner_pid"),
        _canonical_player("loser_id").alias("loser_pid"),
        (pl.lit(f"{SACKMANN_NS}:") + pl.col("tourney_id_clean")).alias("tournament_id"),
        _int("draw_size", pl.Int32()).alias("draw_size_int"),
        _int("best_of", pl.Int8()).alias("best_of_int"),
        _int("match_num", pl.Int32()).alias("source_match_num"),
    )
    tiers = {lvl: ref.tier(lvl) for lvl in ok["tourney_level"].unique().to_list()}
    stages = {code: info.stage for code, info in ref.rounds.items()}
    draw_cat = [
        f"{tiers[level]}_{stages[rnd]}"
        for level, rnd in ok.select("tourney_level", "round_clean").iter_rows()
    ]
    ok = ok.with_columns(pl.Series("draw_category", draw_cat, dtype=pl.String))
    base_keys = [
        match_key(tournament_id=t, season=s, draw_category=d, round_code=r, player_1=w, player_2=lo)
        for t, s, d, r, w, lo in ok.select(
            "tournament_id", "season", "draw_category", "round_clean", "winner_pid", "loser_pid"
        ).iter_rows()
    ]
    ok = ok.with_columns(pl.Series("base_key", base_keys, dtype=pl.String))

    # Collision resolution (ARCHITECTURE.md §4.1).
    ok = ok.with_columns(
        pl.len().over("base_key").alias("_group_n"),
        pl.col("source_match_num").n_unique().over("base_key").alias("_mn_unique"),
        pl.col("source_match_num").null_count().over("base_key").alias("_mn_nulls"),
    )
    resolvable = (pl.col("_mn_nulls") == 0) & (pl.col("_mn_unique") == pl.col("_group_n"))
    colliding = ok.filter((pl.col("_group_n") > 1) & ~resolvable)
    rejected.append(
        _rejections(
            colliding,
            "KEY_COLLISION",
            pl.concat_str([pl.lit("base_key="), pl.col("base_key")]),
        )
    )
    ok = ok.filter((pl.col("_group_n") == 1) | resolvable)
    keys = [
        base if n == 1 else discriminated_match_key(base, int(mn))
        for base, n, mn in ok.select("base_key", "_group_n", "source_match_num").iter_rows()
    ]
    ok = ok.with_columns(
        pl.Series("match_key", keys, dtype=pl.String),
        pl.when(pl.col("_group_n") == 1)
        .then(pl.lit("base"))
        .otherwise(pl.lit("match_num_discriminator"))
        .alias("key_resolution"),
    )
    notes["collision_groups_resolved_by_match_num"] = ok.filter(pl.col("_group_n") > 1)[
        "base_key"
    ].n_unique()

    # Orientation from key and unordered pair only.
    oriented = [
        orient(k, w, lo)
        for k, w, lo in ok.select("match_key", "winner_pid", "loser_pid").iter_rows()
    ]
    ok = ok.with_columns(
        pl.Series("player_a_id", [o[0] for o in oriented], dtype=pl.String),
        pl.Series("player_b_id", [o[1] for o in oriented], dtype=pl.String),
    ).with_columns(
        pl.col("winner_pid").alias("winner_id"),
        (pl.col("player_a_id") == pl.col("winner_pid")).alias("a_won"),
    )

    tournaments = _tournament_table(ok, ref)
    ok = _score_columns(ok)
    ok = ok.join(
        tournaments.select("tournament_id", "event_class", "end_date_bound"),
        on="tournament_id",
        how="left",
    )
    bounds = [
        ref.bounds(ec, rnd) for ec, rnd in ok.select("event_class", "round_clean").iter_rows()
    ]
    rounds = ref.rounds
    ok = (
        ok.with_columns(
            pl.Series("_lo", [b[0] for b in bounds], dtype=pl.Int32),
            pl.Series("_hi", [b[1] for b in bounds], dtype=pl.Int32),
            pl.col("round_clean")
            .replace_strict({c: i.ordinal for c, i in rounds.items()}, return_dtype=pl.Int16)
            .alias("round_ordinal"),
            pl.col("round_clean")
            .replace_strict(
                {c: i.precedence_eligible for c, i in rounds.items()}, return_dtype=pl.Boolean
            )
            .alias("precedence_eligible"),
        )
        .with_columns(
            (pl.col("start_date") + pl.duration(days=pl.col("_lo")))
            .cast(pl.Date)
            .alias("match_day_lo"),
            (pl.col("start_date") + pl.duration(days=pl.col("_hi")))
            .cast(pl.Date)
            .alias("match_day_hi"),
        )
        .with_columns(
            earliest_instant_expr("match_day_lo", chrono).alias("prediction_cutoff_utc"),
            latest_instant_expr("match_day_hi", chrono).alias("knowable_at_utc"),
        )
    )

    # A player appearing twice in one knockout round of one event breaks draw logic.
    sides = pl.concat(
        [
            ok.select("match_key", "tournament_id", "round_clean", pl.col(c).alias("pid"))
            for c in ("player_a_id", "player_b_id")
        ]
    )
    dup_rounds = (
        sides.filter(pl.col("round_clean") != "RR")
        .group_by("tournament_id", "round_clean", "pid")
        .agg(pl.col("match_key"), pl.len().alias("n"))
        .filter(pl.col("n") > 1)
    )
    conflicted = set(dup_rounds.explode("match_key")["match_key"].to_list())
    ok = ok.with_columns(pl.col("match_key").is_in(list(conflicted)).alias("precedence_conflict"))

    null_utc = pl.lit(None, dtype=schemas.UTC_DT)
    matches = ok.select(
        "match_key", "key_resolution", "tournament_id", "season", "draw_category",
        pl.col("round_clean").alias("round"), "round_ordinal", "precedence_eligible",
        "precedence_conflict", pl.col("best_of_int").alias("best_of"), "player_a_id",
        "player_b_id", "source_match_num", "event_class",
        pl.col("start_date").alias("tournament_start_date"),
        pl.col("end_date_bound").alias("tournament_end_bound"), "match_day_lo", "match_day_hi",
        pl.lit(None, dtype=pl.Date).alias("match_date"),
        null_utc.alias("scheduled_start_utc"), null_utc.alias("actual_start_utc"),
        null_utc.alias("completed_at_utc"), null_utc.alias("published_at_utc"),
        "prediction_cutoff_utc", "knowable_at_utc",
        pl.lit("day_bounds").alias("knowability_rule"), "winner_id", "a_won", "outcome_type",
        pl.col("score").alias("score_raw"), "score_status", "n_sets", "sets_a", "sets_b",
        "games_a", "games_b", "tiebreak_sets", "has_match_tiebreak",
        _int("minutes", pl.Int32()).alias("minutes"),
        pl.lit(LicenceClass.RESEARCH_NC.value).alias("licence_class"),
        pl.lit(SOURCE).alias("source"), "source_file", "source_row_sha256",
    )  # fmt: skip

    entry, stats = _per_player_tables(ok)
    notes["stats_values_unparseable"] = _count_unparseable(
        ok, [f"{p}_{k}" for p in ("w", "l") for k in _STAT_FIELDS]
    )
    tables = {
        "matches": matches,
        "match_entry": entry,
        "match_stats": stats,
        "tournaments": tournaments.select(schemas.column_names("tournaments")),
    }
    return tables, pl.concat(rejected, how="vertical"), notes


def _count_unparseable(frame: pl.DataFrame, columns: list[str]) -> int:
    total = 0
    for c in columns:
        total += frame.filter(
            pl.col(c).is_not_null() & ~pl.col(c).str.contains(r"^\s*[0-9]+\s*$")
        ).height
    return total


def _per_player_tables(ok: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Pivot winner/loser columns into side-neutral per-player rows."""
    entry_parts = []
    stat_parts = []
    for role, stat_prefix, opp_prefix in (("winner", "w", "l"), ("loser", "l", "w")):
        pid = pl.col(f"{role}_pid")
        side = pl.when(pid == pl.col("player_a_id")).then(pl.lit("A")).otherwise(pl.lit("B"))
        entry_parts.append(
            ok.select(
                "match_key",
                pid.alias("player_id"),
                side.alias("side"),
                _int(f"{role}_seed", pl.Int32()).alias("seed"),
                _strip(f"{role}_entry").alias("entry"),
                _int(f"{role}_rank", pl.Int32()).alias("rank_at_event_start"),
                _int(f"{role}_rank_points", pl.Int32()).alias("rank_points_at_event_start"),
                _strip(f"{role}_age").cast(pl.Float64, strict=False).alias("age_at_event_start"),
                _int(f"{role}_ht", pl.Int16()).alias("height_cm_reported"),
                _strip(f"{role}_hand").alias("hand_reported"),
                _strip(f"{role}_ioc").alias("country_reported"),
                _strip(f"{role}_name").alias("name_reported"),
            )
        )
        own = {v: _int(f"{stat_prefix}_{k}", pl.Int16()) for k, v in _STAT_FIELDS.items()}
        opp_svpt = _int(f"{opp_prefix}_svpt", pl.Int16())
        opp_won = _int(f"{opp_prefix}_1stWon", pl.Int16()) + _int(
            f"{opp_prefix}_2ndWon", pl.Int16()
        )
        stat_parts.append(
            ok.select(
                "match_key",
                pid.alias("player_id"),
                side.alias("side"),
                *[expr.alias(name) for name, expr in own.items()],
                opp_svpt.alias("return_points_played"),
                (opp_svpt - opp_won).cast(pl.Int16).alias("return_points_won"),
                pl.col("knowable_at_utc"),
            )
        )
    entry = pl.concat(entry_parts).select(schemas.column_names("match_entry"))
    stats = pl.concat(stat_parts)
    stat_cols = list(_STAT_FIELDS.values())
    stats = stats.filter(pl.any_horizontal([pl.col(c).is_not_null() for c in stat_cols]))
    return entry, stats.select(schemas.column_names("match_stats"))


# --------------------------------------------------------------------------- players


def build_players(snapshot: Path) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, Any]]:
    raw = read_raw_csv(snapshot / PLAYERS_FILE, RAW_PLAYER_COLUMNS, PLAYERS_FILE)
    raw = raw.with_columns(row_hashes(raw, RAW_PLAYER_COLUMNS, "qe.source_row.v1"))
    valid_id = pl.col("player_id").str.contains(_INT).fill_null(False)
    rejected = [_rejections(raw.filter(~valid_id), "INVALID_PLAYER_ID", None)]
    ok = raw.filter(valid_id).with_columns(
        _strip("player_id").cast(pl.Int64).alias("source_player_id")
    )
    ok = _keep_first(ok, "source_row_sha256", ["source_row_number"])
    rejected.append(_rejections(ok.filter(pl.col("_dup") > 1), "DUPLICATE_ROW", None))
    ok = ok.filter(pl.col("_dup") == 1)
    ok = ok.with_columns(pl.len().over("source_player_id").alias("_n"))
    rejected.append(
        _rejections(
            ok.filter(pl.col("_n") > 1), "PLAYER_CONFLICT", pl.lit("id with differing rows")
        )
    )
    ok = ok.filter(pl.col("_n") == 1)

    dob = _strip("dob")
    full = dob.str.strptime(pl.Date, "%Y%m%d", strict=False)
    year_only = dob.str.contains(r"^[0-9]{4}0000$").fill_null(False)
    height = _int("height", pl.Int16())
    players = ok.select(
        (pl.lit(f"{SACKMANN_NS}:") + pl.col("source_player_id").cast(pl.String)).alias("player_id"),
        "source_player_id",
        _strip("name_first").alias("name_first"),
        _strip("name_last").alias("name_last"),
        pl.when(_strip("hand").is_in(["R", "L", "A"]))
        .then(_strip("hand"))
        .otherwise(pl.lit("U"))
        .alias("hand"),
        full.alias("birth_date"),
        pl.when(full.is_not_null())
        .then(full.dt.year())
        .when(year_only)
        .then(dob.str.slice(0, 4).cast(pl.Int32, strict=False))
        .otherwise(None)
        .cast(pl.Int16)
        .alias("birth_year"),
        pl.when(full.is_not_null())
        .then(pl.lit("day"))
        .when(year_only)
        .then(pl.lit("year"))
        .otherwise(pl.lit("unknown"))
        .alias("birth_date_precision"),
        _strip("ioc").alias("country_ioc"),
        pl.when(height.is_between(100, 250)).then(height).otherwise(None).alias("height_cm"),
        _strip("wikidata_id").alias("wikidata_id"),
        pl.lit(LicenceClass.RESEARCH_NC.value).alias("licence_class"),
        pl.lit(SOURCE).alias("source"),
        "source_file",
        "source_row_sha256",
    )
    notes = {
        "player_heights_out_of_range": ok.filter(
            height.is_not_null() & ~height.is_between(100, 250)
        ).height,
        "player_dob_unparseable": ok.filter(dob.is_not_null() & full.is_null() & ~year_only).height,
    }
    return players, pl.concat(rejected), notes


# --------------------------------------------------------------------------- rankings


def build_rankings(
    snapshot: Path, manifest: RawManifest, chrono: ChronologyConfig
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, Any]]:
    frames = [
        read_raw_csv(snapshot / e.path, RAW_RANKING_COLUMNS, e.path)
        for e in manifest.files
        if RANKINGS_FILE.match(e.path)
    ]
    raw = pl.concat(frames)
    raw = raw.with_columns(row_hashes(raw, RAW_RANKING_COLUMNS, "qe.source_row.v1"))
    parsed_date = pl.col("ranking_date").str.strptime(pl.Date, "%Y%m%d", strict=False)
    reason = _first_failure(
        [
            (
                parsed_date.is_null() | ~pl.col("ranking_date").str.contains(r"^[0-9]{8}$"),
                "INVALID_RANKING_DATE",
            ),
            (~pl.col("player").str.contains(_INT).fill_null(False), "INVALID_PLAYER_ID"),
            (
                ~pl.col("rank").str.contains(_INT).fill_null(False)
                | (_int("rank", pl.Int32()) < 1).fill_null(True),
                "INVALID_RANK",
            ),
        ]
    )
    staged = raw.with_columns(reason.alias("_reason"))
    rejected = [
        _rejections(staged.filter(pl.col("_reason").is_not_null()), pl.col("_reason"), None)
    ]
    ok = staged.filter(pl.col("_reason").is_null()).with_columns(
        parsed_date.alias("ranking_date_parsed"),
        _canonical_player("player").alias("player_id"),
        _int("rank", pl.Int32()).alias("rank_int"),
        _int("points", pl.Int32()).alias("points_int"),
    )
    ok = _keep_first(ok, "source_row_sha256", ["source_file", "source_row_number"])
    rejected.append(_rejections(ok.filter(pl.col("_dup") > 1), "DUPLICATE_ROW", None))
    ok = ok.filter(pl.col("_dup") == 1)
    ok = ok.with_columns(pl.len().over("player_id", "ranking_date_parsed").alias("_n"))
    rejected.append(
        _rejections(
            ok.filter(pl.col("_n") > 1),
            "RANKING_CONFLICT",
            pl.lit("same player and date with differing rank/points"),
        )
    )
    ok = ok.filter(pl.col("_n") == 1)
    rankings = ok.select(
        "player_id",
        pl.col("ranking_date_parsed").alias("ranking_date"),
        pl.col("rank_int").alias("rank"),
        pl.col("points_int").alias("points"),
        latest_instant_expr("ranking_date_parsed", chrono).alias("knowable_at_utc"),
        pl.lit(LicenceClass.RESEARCH_NC.value).alias("licence_class"),
        pl.lit(SOURCE).alias("source"),
        "source_file",
        "source_row_sha256",
    )
    notes = {
        "ranking_points_unparseable": ok.filter(
            pl.col("points").is_not_null() & pl.col("points_int").is_null()
        ).height
    }
    return rankings, pl.concat(rejected), notes


# --------------------------------------------------------------------------- orchestration


def _git_state(repo_root: Path) -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return commit, dirty


def _reconcile(
    manifest: RawManifest, canonical: dict[str, pl.DataFrame], rejected: pl.DataFrame,
    raw_counts: dict[str, int],
) -> list[dict[str, Any]]:  # fmt: skip
    out_counts: dict[str, int] = {}
    for table in ("matches", "players", "rankings"):
        for file, n in canonical[table].group_by("source_file").len().iter_rows():
            out_counts[file] = out_counts.get(file, 0) + n
    rej_counts = dict(rejected.group_by("source_file").len().iter_rows())
    rows = []
    for entry in manifest.files:
        rows_in = raw_counts.get(entry.path, 0)
        rows_out = out_counts.get(entry.path, 0)
        rows_rej = rej_counts.get(entry.path, 0)
        if rows_in != rows_out + rows_rej:
            raise ReconciliationError(
                f"{entry.path}: {rows_in} in != {rows_out} canonical + {rows_rej} rejected"
            )
        rows.append(
            {"file": entry.path, "rows_in": rows_in, "rows_canonical": rows_out,
             "rows_rejected": rows_rej}
        )  # fmt: skip
    return rows


def build(
    snapshot: Path,
    manifest: RawManifest,
    out_root: Path,
    *,
    chrono: ChronologyConfig | None = None,
    repo_root: Path | None = None,
) -> BuildResult:
    chrono = chrono or ChronologyConfig()
    ref = load_reference()

    raw_matches = _read_matches(snapshot, manifest)
    match_tables, rej_matches, notes = build_matches(raw_matches, ref, chrono)
    players, rej_players, player_notes = build_players(snapshot)
    rankings, rej_rankings, ranking_notes = build_rankings(snapshot, manifest, chrono)
    notes |= player_notes | ranking_notes

    rejected = pl.concat([rej_matches, rej_players, rej_rankings], how="vertical")
    tables: dict[str, pl.DataFrame] = {
        **match_tables,
        "players": players,
        "rankings": rankings,
        "rejected_rows": rejected,
    }
    for name in list(tables):
        frame = (
            tables[name]
            .select(schemas.column_names(name))
            .cast(
                schemas.dtypes(name)  # type: ignore[arg-type]
            )
        )
        tables[name] = schemas.validate_table(name, frame).sort(list(schemas.PRIMARY_KEYS[name]))

    # rows_in is counted by an independent scan of each raw file, not derived from outputs.
    raw_counts = {e.path: _count_csv_rows(snapshot / e.path) for e in manifest.files}
    reconciliation = _reconcile(manifest, tables, rejected, raw_counts)

    reference_fp = sha256_fields(
        "qe.reference_and_chrono.v1", ref.fingerprint, chrono.model_dump_json()
    )
    code_fp = code_fingerprint()
    build_id = sha256_fields(
        "qe.build.v2", manifest.fingerprint(), reference_fp, __version__, code_fp
    )[:16]
    out_dir = out_root / build_id
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, frame in sorted(tables.items()):
        file = f"{name}.parquet"
        frame.write_parquet(out_dir / file, compression="zstd", statistics=False)
        excludes = schemas.content_hash_excludes(name)
        entries.append(
            TableEntry(
                name=name,
                file=file,
                rows=frame.height,
                content_sha256=frame_content_hash(frame, exclude=excludes),
                content_hash_excludes=excludes,
            )
        )
    git_commit, git_dirty = _git_state(repo_root) if repo_root else (None, None)
    build_manifest = BuildManifest(
        build_id=build_id,
        raw_manifest_fingerprint=manifest.fingerprint(),
        reference_fingerprint=reference_fp,
        code_version=__version__,
        code_fingerprint=code_fp,
        git_commit=git_commit,
        git_dirty=git_dirty,
        python_version=sys.version.split()[0],
        polars_version=pl.__version__,
        licence_lineage=(manifest.licence_class,),
        tables=tuple(entries),
        reconciliation=tuple(reconciliation),
        notes=notes,
    )
    write_json(out_dir / BUILD_MANIFEST_NAME, build_manifest)
    (out_root / LATEST_POINTER).write_text(build_id + "\n", encoding="utf-8", newline="\n")
    return BuildResult(build_id, out_dir, tables, build_manifest)


def code_fingerprint() -> str:
    """Hash of every Python source file in the package (line endings normalised).

    Any code change yields a new build ID, so different outputs can never share an ID.
    """
    package_root = Path(__file__).resolve().parents[1]
    parts = []
    for file in sorted(package_root.rglob("*.py")):
        rel = file.relative_to(package_root).as_posix()
        body = file.read_bytes().replace(b"\r\n", b"\n")
        parts.append(f"{rel}={sha256_fields('qe.source_file.v1', body.decode('utf-8'))}")
    return sha256_fields("qe.code.v1", *parts)


def _count_csv_rows(path: Path) -> int:
    """Data rows in a CSV, counted by the CSV reader independently of any transformation."""
    return pl.scan_csv(path, infer_schema=False).select(pl.len()).collect().item()  # type: ignore[no-any-return]


def load_tables(build_dir: Path) -> dict[str, pl.DataFrame]:
    return {name: pl.read_parquet(build_dir / f"{name}.parquet") for name in schemas.TABLES}


def resolve_build_dir(out_root: Path, build_id: str | None = None) -> Path:
    if build_id is None:
        build_id = (out_root / LATEST_POINTER).read_text(encoding="utf-8").strip()
    return out_root / build_id


__all__ = [
    "BuildResult",
    "ReconciliationError",
    "SchemaDriftError",
    "build",
    "build_matches",
    "load_tables",
    "resolve_build_dir",
]
