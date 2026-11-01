"""Data-quality report for a canonical build (JSON + Markdown).

Holdout rows (on/after ``holdout_start``) receive integrity checks only. Outcome-dependent
statistics (win rates, orientation tests, draw-logic checks that use winners) are computed on
development rows only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from quantedge.core.manifest import BUILD_MANIFEST_NAME, read_build_manifest
from quantedge.core.splits import SplitConfig
from quantedge.quality.orientation import orientation_checks

HOLDOUT_SECTION_KEYS = frozenset(
    {
        "period",
        "matches",
        "tournaments",
        "max_tournament_start_date",
        "score_status_counts",
        "coverage_by_year_level",
    }
)

STATS_RULES: dict[str, pl.Expr] = {
    "first_in_le_serve_points": pl.col("first_serves_in") <= pl.col("serve_points"),
    "first_won_le_first_in": pl.col("first_serve_points_won") <= pl.col("first_serves_in"),
    "second_won_le_second_serves": pl.col("second_serve_points_won")
    <= pl.col("serve_points") - pl.col("first_serves_in"),
    "double_faults_le_second_serves": pl.col("double_faults")
    <= pl.col("serve_points") - pl.col("first_serves_in"),
    "aces_le_serve_points": pl.col("aces") <= pl.col("serve_points"),
    "bp_saved_le_bp_faced": pl.col("break_points_saved") <= pl.col("break_points_faced"),
    "return_won_within_played": (pl.col("return_points_won") >= 0)
    & (pl.col("return_points_won") <= pl.col("return_points_played")),
}


def _counts(frame: pl.DataFrame, column: str) -> dict[str, int]:
    return {
        str(k): int(v)
        for k, v in frame.group_by(column).len().sort(column, nulls_last=True).iter_rows()
    }


def _coverage(matches: pl.DataFrame, entries: pl.DataFrame, stats: pl.DataFrame,
              tournaments: pl.DataFrame) -> list[dict[str, Any]]:  # fmt: skip
    ranked = entries.group_by("match_key").agg(
        pl.col("rank_at_event_start").is_not_null().all().alias("both_ranked")
    )
    has_stats = stats.select("match_key").unique().with_columns(pl.lit(True).alias("has_stats"))
    frame = (
        matches.join(tournaments.select("tournament_id", "level", "surface"), on="tournament_id")
        .join(ranked, on="match_key", how="left")
        .join(has_stats, on="match_key", how="left")
        .with_columns(pl.col("tournament_start_date").dt.year().alias("year"))
    )
    out = (
        frame.group_by("year", "level")
        .agg(
            pl.len().alias("matches"),
            pl.col("has_stats").fill_null(False).mean().round(4).alias("share_with_stats"),
            pl.col("both_ranked").fill_null(False).mean().round(4).alias("share_both_ranked"),
            pl.col("minutes").is_not_null().mean().round(4).alias("share_with_minutes"),
            pl.col("surface").is_not_null().mean().round(4).alias("share_with_surface"),
            (pl.col("score_status") == "ok").mean().round(4).alias("share_score_ok"),
        )
        .sort("year", "level", nulls_last=True)
    )
    return out.to_dicts()


def _overlapping_events(matches: pl.DataFrame, tournaments: pl.DataFrame) -> dict[str, Any]:
    sides = pl.concat(
        [
            matches.select("tournament_id", pl.col(c).alias("player_id"))
            for c in ("player_a_id", "player_b_id")
        ]
    ).unique()
    frame = (
        sides.join(
            tournaments.select("tournament_id", "start_date", "nominal_days"), on="tournament_id"
        )
        .with_columns(
            (pl.col("start_date") + pl.duration(days=pl.col("nominal_days") - 1))
            .cast(pl.Date)
            .alias("nominal_end")
        )
        .sort("player_id", "start_date", "tournament_id")
        .with_columns(pl.col("nominal_end").cum_max().shift(1).over("player_id").alias("prev_end"))
        .with_columns(
            (pl.col("start_date") <= pl.col("prev_end")).fill_null(False).alias("overlap")
        )
    )
    flagged = frame.filter(pl.col("overlap"))
    return {
        "player_event_entries_overlapping_previous": flagged.height,
        "players_with_overlaps": flagged["player_id"].n_unique(),
        "note": "Nominal event intervals; overlaps are expected around two-week events and are "
        "excluded from cross-event knowability until (K) holds.",
    }


def _rank_cross_check(entries: pl.DataFrame, matches: pl.DataFrame,
                      rankings: pl.DataFrame) -> dict[str, Any]:  # fmt: skip
    ranked = (
        entries.filter(pl.col("rank_at_event_start").is_not_null())
        .join(matches.select("match_key", "tournament_start_date"), on="match_key")
        .sort("tournament_start_date")
    )
    table = rankings.select(
        "player_id", pl.col("ranking_date"), pl.col("rank").alias("table_rank")
    ).sort("ranking_date")
    joined = ranked.join_asof(
        table, left_on="tournament_start_date", right_on="ranking_date", by="player_id",
        strategy="backward", check_sortedness=False,  # both sides sorted on the asof key above
    )  # fmt: skip
    missing = joined.filter(pl.col("table_rank").is_null()).height
    found = joined.filter(pl.col("table_rank").is_not_null())
    return {
        "entries_with_source_rank": ranked.height,
        "no_ranking_row_on_or_before_event_start": missing,
        "source_rank_differs_from_latest_table_rank": found.filter(
            pl.col("table_rank") != pl.col("rank_at_event_start")
        ).height,
    }


def _knockout_violations(matches: pl.DataFrame) -> int:
    """Development only: players who lost a knockout round yet appear in a later main round."""
    main = matches.filter(
        pl.col("draw_category").str.ends_with("_main")
        & pl.col("precedence_eligible")
        & (pl.col("round") != "RR")
    )
    losers = main.select(
        "tournament_id",
        pl.when(pl.col("a_won"))
        .then(pl.col("player_b_id"))
        .otherwise(pl.col("player_a_id"))
        .alias("player_id"),
        pl.col("round_ordinal").alias("lost_at"),
        pl.col("round").alias("lost_round"),
    )
    appearances = pl.concat(
        [main.select("tournament_id", "round", "round_ordinal", pl.col(c).alias("player_id"))
         for c in ("player_a_id", "player_b_id")]
    )  # fmt: skip
    later = losers.join(appearances, on=["tournament_id", "player_id"]).filter(
        (pl.col("round_ordinal") > pl.col("lost_at"))
        & ~((pl.col("lost_round") == "SF") & (pl.col("round") == "BR"))
    )
    return later.height


def build_report(
    tables: dict[str, pl.DataFrame], build_dir: Path, splits: SplitConfig
) -> dict[str, Any]:
    manifest = read_build_manifest(build_dir / BUILD_MANIFEST_NAME)
    matches = tables["matches"]
    entries = tables["match_entry"]
    stats = tables["match_stats"]
    tournaments = tables["tournaments"]
    rankings = tables["rankings"]
    rejected = tables["rejected_rows"]
    players = tables["players"]

    holdout_start = splits.holdout_start
    dev_m = matches.filter(pl.col("tournament_start_date") < holdout_start)
    hold_m = matches.filter(pl.col("tournament_start_date") >= holdout_start)
    dev_keys, hold_keys = dev_m.select("match_key"), hold_m.select("match_key")

    def section(m: pl.DataFrame, keys: pl.DataFrame) -> dict[str, Any]:
        t_ids = m.select("tournament_id").unique()
        return {
            "matches": m.height,
            "tournaments": t_ids.height,
            "max_tournament_start_date": str(m["tournament_start_date"].max()),
            "score_status_counts": _counts(m, "score_status"),
            "coverage_by_year_level": _coverage(
                m,
                entries.join(keys, on="match_key", how="semi"),
                stats.join(keys, on="match_key", how="semi"),
                tournaments.join(t_ids, on="tournament_id", how="semi"),
            ),
        }

    stats_violations = {
        name: stats.filter(rule.not_()).height for name, rule in STATS_RULES.items()
    }
    known_players = players.select("player_id")
    match_players = pl.concat(
        [matches.select(pl.col(c).alias("player_id")) for c in ("player_a_id", "player_b_id")]
    ).unique()

    development = section(dev_m, dev_keys) | {
        "period": f"<= {splits.development_end}",
        "outcome_type_counts": _counts(dev_m, "outcome_type"),
        "orientation_checks": orientation_checks(dev_m, entries, tournaments),
        "knockout_logic_violations": _knockout_violations(dev_m),
    }
    holdout = section(hold_m, hold_keys) | {"period": f">= {holdout_start}"}
    assert set(holdout) <= HOLDOUT_SECTION_KEYS

    unknown_levels = sorted(
        str(x) for x in tournaments.filter(pl.col("tier") == "other")["level"].unique().to_list()
    )
    report: dict[str, Any] = {
        "build_id": manifest.build_id,
        "licence_lineage": [str(c) for c in manifest.licence_lineage],
        "snapshot": {
            "raw_manifest_fingerprint": manifest.raw_manifest_fingerprint,
            "files": len(manifest.reconciliation),
            "max_tournament_start_date": str(matches["tournament_start_date"].max()),
            "min_tournament_start_date": str(matches["tournament_start_date"].min()),
            "max_ranking_date": str(rankings["ranking_date"].max()),
        },
        "table_rows": {t.name: t.rows for t in manifest.tables},
        "reconciliation_totals": {
            k: sum(int(r[k]) for r in manifest.reconciliation)
            for k in ("rows_in", "rows_canonical", "rows_rejected")
        },
        "rejections_by_reason": _counts(rejected, "reason_code"),
        "rejections_by_file_kind": {
            str(k): int(v)
            for k, v in rejected.with_columns(
                pl.col("source_file")
                .str.replace(r"_\d{4}\.csv$|_[0-9a-z]+\.csv$|\.csv$", "")
                .alias("kind")
            )
            .group_by("kind", "reason_code")
            .len()
            .select(pl.concat_str(["kind", "reason_code"], separator=":"), "len")
            .sort("kind")
            .iter_rows()
        },
        "key_resolution_counts": _counts(matches, "key_resolution"),
        "precedence_conflict_matches": int(matches["precedence_conflict"].sum()),
        "codes": {
            "levels_without_reference_tier": unknown_levels,
            "event_class_counts": _counts(tournaments, "event_class"),
            "surface_counts": _counts(tournaments, "surface"),
            "round_counts": _counts(matches, "round"),
            "entry_code_counts": _counts(entries, "entry"),
        },
        "tournament_attribute_conflicts": int((tournaments["attribute_conflicts"] > 0).sum()),
        "stats_consistency_violations": stats_violations,
        "players": {
            "rows": players.height,
            "match_players_missing_from_players_table": match_players.join(
                known_players, on="player_id", how="anti"
            ).height,
            "birth_date_precision": _counts(players, "birth_date_precision"),
            "hand": _counts(players, "hand"),
        },
        "rankings": {"rows": rankings.height, "players": rankings["player_id"].n_unique()},
        "rank_cross_check": _rank_cross_check(entries, matches, rankings),
        "overlapping_events": _overlapping_events(matches, tournaments),
        "build_notes": manifest.notes,
        "development": development,
        "holdout_integrity_only": holdout,
    }
    return report


def _md_table(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(str(r.get(c, "")) for c in columns) + " |" for r in rows]
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# QA report — build `{report['build_id']}`", ""]
    lines += [f"Licence lineage: {', '.join(report['licence_lineage'])}", ""]
    for title, key in (
        ("Snapshot", "snapshot"),
        ("Table rows", "table_rows"),
        ("Reconciliation totals", "reconciliation_totals"),
        ("Rejections by reason", "rejections_by_reason"),
        ("Rejections by file kind", "rejections_by_file_kind"),
        ("Key resolution", "key_resolution_counts"),
        ("Stats consistency violations", "stats_consistency_violations"),
        ("Players", "players"),
        ("Rankings", "rankings"),
        ("Rank cross-check", "rank_cross_check"),
        ("Overlapping events", "overlapping_events"),
        ("Build notes", "build_notes"),
    ):
        lines += [
            f"## {title}",
            "",
            "```json",
            json.dumps(report[key], indent=2, default=str),
            "```",
            "",
        ]
    lines += ["## Codes", "", "```json", json.dumps(report["codes"], indent=2), "```", ""]
    dev = report["development"]
    lines += ["## Development period", ""]
    lines += [
        f"- Matches: {dev['matches']}",
        f"- Max start date: {dev['max_tournament_start_date']}",
    ]
    lines += [
        f"- Score status: {dev['score_status_counts']}",
        f"- Outcome types: {dev['outcome_type_counts']}",
    ]
    lines += [f"- Knockout-logic violations: {dev['knockout_logic_violations']}", ""]
    oc = dev["orientation_checks"]
    lines += [f"### Orientation checks (alpha={oc['alpha']}, Holm) — passed: {oc['passed']}", ""]
    lines += _md_table(
        oc["tests"], ["name", "n", "successes", "rate", "p_value", "holm_adjusted_p", "passed"]
    )
    lines += ["", "### Coverage by year and level (development)", ""]
    cov_cols = ["year", "level", "matches", "share_with_stats", "share_both_ranked",
                "share_with_minutes", "share_with_surface", "share_score_ok"]  # fmt: skip
    lines += _md_table(dev["coverage_by_year_level"], cov_cols)
    hold = report["holdout_integrity_only"]
    lines += ["", "## Holdout (integrity checks only)", ""]
    lines += [
        f"- Matches: {hold['matches']}",
        f"- Max start date: {hold['max_tournament_start_date']}",
    ]
    lines += [f"- Score status: {hold['score_status_counts']}", ""]
    lines += _md_table(hold["coverage_by_year_level"], cov_cols)
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], build_dir: Path) -> tuple[Path, Path]:
    json_path = build_dir / "qa_report.json"
    md_path = build_dir / "qa_report.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return json_path, md_path
