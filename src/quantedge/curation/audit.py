"""Gate 0 audit report: traces every Phase 1 anomaly to the policy that handles it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from quantedge.core.splits import SplitConfig
from quantedge.curation.eligibility import (
    POLICY_VERSION,
    build_eligibility,
    load_curation_reference,
    suspicious_identities,
)

DERIVED_DIR = "derived"
ELIGIBILITY_FILE = "eligibility.parquet"


def _counts(frame: pl.DataFrame, *by: str) -> list[dict[str, Any]]:
    return (
        frame.group_by(*by).len().sort("len", *by, descending=[True] + [False] * len(by)).to_dicts()
    )


def _phase1_knockout_pairs(matches: pl.DataFrame) -> pl.DataFrame:
    """The Phase 1 QA definition of 'appearance after a knockout loss', at pair level."""
    main = matches.filter(
        pl.col("draw_category").str.ends_with("_main")
        & pl.col("precedence_eligible")
        & (pl.col("round") != "RR")
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
    appearances = pl.concat(
        [
            main.select(
                "tournament_id", "round", "round_ordinal", "match_key", pl.col(c).alias("pid")
            )
            for c in ("player_a_id", "player_b_id")
        ]
    )
    return losers.join(appearances, on=["tournament_id", "pid"]).filter(
        (pl.col("round_ordinal") > pl.col("lost_at"))
        & ~((pl.col("lost_round") == "SF") & (pl.col("round") == "BR"))
    )


def _classify_knockout_pairs(pairs: pl.DataFrame, elig: pl.DataFrame) -> list[dict[str, Any]]:
    flags = elig.select(
        "match_key",
        "team_event",
        "placeholder_player",
        "duplicate_record",
        "contradictory_duplicate",
        "round_label_conflict",
        "knockout_contradiction",
        "primary_reason",
    )
    joined = pairs.join(
        flags.rename(lambda c: f"loss_{c}"), left_on="loss_key", right_on="loss_match_key"
    ).join(flags.rename(lambda c: f"later_{c}"), left_on="match_key", right_on="later_match_key")

    def either(flag: str) -> pl.Expr:
        return pl.col(f"loss_{flag}") | pl.col(f"later_{flag}")

    category = (
        pl.when(pl.col("loss_team_event"))
        .then(pl.lit("team_event_legitimate"))
        .when(pl.col("pid").is_in(list(load_curation_reference().placeholder_ids)))
        .then(pl.lit("placeholder_identity_shared_by_different_players"))
        .when(either("duplicate_record") | either("contradictory_duplicate"))
        .then(pl.lit("resolved_by_duplicate_policy"))
        .when(either("round_label_conflict"))
        .then(pl.lit("round_label_conflict_deferred"))
        .when(either("knockout_contradiction"))
        .then(pl.lit("unresolved_knockout_contradiction_excluded"))
        .otherwise(pl.lit("other"))
    )
    return _counts(joined.with_columns(category.alias("category")), "category")


def build_audit(
    matches: pl.DataFrame,
    tournaments: pl.DataFrame,
    players: pl.DataFrame,
    eligibility: pl.DataFrame,
    splits: SplitConfig,
) -> dict[str, Any]:
    frame = matches.join(eligibility, on="match_key")
    if frame.height != matches.height:
        raise AssertionError("eligibility must cover every match exactly once")
    dev = frame.filter(pl.col("tournament_start_date") <= splits.development_end)
    holdout = frame.filter(pl.col("tournament_start_date") >= splits.holdout_start)
    dev_years = dev.filter(
        pl.col("tournament_start_date") >= splits.development_start
    ).with_columns(pl.col("tournament_start_date").dt.year().alias("year"))
    used = pl.concat([matches["player_a_id"], matches["player_b_id"]])
    candidates = suspicious_identities(players, used)
    curated = load_curation_reference()
    return {
        "policy_version": POLICY_VERSION,
        "curation_reference_fingerprint": curated.fingerprint,
        "matches_total": matches.height,
        "development_through_dev_end": {
            "matches": dev.height,
            "by_primary_reason": _counts(dev, "primary_reason"),
            "elo_update_baseline": int(dev["elo_update_baseline"].sum()),
            "elo_update_retirements_variant": int(dev["elo_update_retirements"].sum()),
            "eval_eligible": int(dev["eval_eligible"].sum()),
            "knowability_deferred": int(dev["knowability_deferred"].sum()),
            "team_event_matches": int(dev["team_event"].sum()),
        },
        "eval_eligible_by_year_2005_2024": {
            str(y): int(n)
            for y, n in dev_years.filter(pl.col("eval_eligible"))
            .group_by("year")
            .len()
            .sort("year")
            .iter_rows()
        },
        "holdout_integrity_only": {
            "matches": holdout.height,
            "by_primary_reason": _counts(holdout, "primary_reason"),
        },
        "phase1_anomalies": {
            "inconsistent_scores_development": _counts(
                dev.filter(pl.col("score_status") == "inconsistent"),
                "score_subclass",
                "primary_reason",
            ),
            "precedence_conflicts_all_years": _counts(
                frame.filter(pl.col("precedence_conflict")), "primary_reason"
            ),
            "precedence_conflicts_development": _counts(
                dev.filter(pl.col("precedence_conflict")), "primary_reason"
            ),
            "knockout_violation_pairs_development": _classify_knockout_pairs(
                _phase1_knockout_pairs(dev), eligibility
            ),
        },
        "identities": {
            "curated_placeholders": sorted(curated.placeholder_ids),
            "rule_candidates_used_in_matches": candidates.to_dicts(),
            "rule_candidates_not_curated": sorted(
                set(candidates["player_id"].to_list()) - set(curated.placeholder_ids)
            ),
        },
    }


def render_audit_markdown(audit: dict[str, Any]) -> str:
    lines = [f"# Gate 0 eligibility audit ({audit['policy_version']})", ""]
    for key, value in audit.items():
        if key in {"policy_version"}:
            continue
        lines += [f"## {key}", "", "```json", json.dumps(value, indent=2, default=str), "```", ""]
    return "\n".join(lines)


def run_audit(
    build_dir: Path, tables: dict[str, pl.DataFrame], splits: SplitConfig
) -> tuple[pl.DataFrame, dict[str, Any]]:
    eligibility = build_eligibility(tables["matches"], tables["tournaments"])
    audit = build_audit(
        tables["matches"], tables["tournaments"], tables["players"], eligibility, splits
    )
    out = build_dir / DERIVED_DIR
    out.mkdir(parents=True, exist_ok=True)
    eligibility.write_parquet(out / ELIGIBILITY_FILE, compression="zstd", statistics=False)
    (out / "eligibility_audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (out / "eligibility_audit.md").write_text(
        render_audit_markdown(audit), encoding="utf-8", newline="\n"
    )
    return eligibility, audit


def load_eligibility(build_dir: Path) -> pl.DataFrame:
    return pl.read_parquet(build_dir / DERIVED_DIR / ELIGIBILITY_FILE)
