"""Integration tests on the synthetic snapshot: AT-05..07, 10, 11, 14, 15, 18..20, 25, 26, 29."""

from __future__ import annotations

import csv
import io
import random
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from quantedge.canonical import schemas
from quantedge.canonical.build import BuildResult, build, build_matches, load_tables
from quantedge.canonical.reference import load_reference
from quantedge.core.chronology import ChronologyConfig, earliest_instant, latest_instant
from quantedge.core.dataview import DataView, HoldoutAccessError
from quantedge.core.lineage import BuildProfile, LicenceError, assert_profile_allows
from quantedge.core.manifest import RawManifest
from quantedge.core.splits import load_split_config
from quantedge.quality.report import HOLDOUT_SECTION_KEYS, build_report
from tests.conftest import REPO_ROOT
from tests.fixtures.synthetic import match_row, materialise, raw_match_frame, synthetic_files

SPLITS = load_split_config(REPO_ROOT / "configs" / "splits.yaml")


def _hashes(result: BuildResult) -> dict[str, str]:
    return {t.name: t.content_sha256 for t in result.manifest.tables}


def _rebuild(tmp_path: Path, files: dict[str, bytes]) -> BuildResult:
    snapshot, manifest, _ = materialise(tmp_path / "data", files)
    return build(snapshot, manifest, tmp_path / "processed")


def _rows(rejected: pl.DataFrame, reason: str) -> list[tuple[str, int]]:
    return sorted(
        rejected.filter(pl.col("reason_code") == reason)
        .select("source_file", "source_row_number")
        .iter_rows()
    )


# ----------------------------------------------------------------------------- AT-05 / AT-06


def test_written_parquet_validates_against_schemas(synthetic_build: BuildResult) -> None:
    loaded = load_tables(synthetic_build.out_dir)
    for name, frame in loaded.items():
        schemas.validate_table(name, frame)
        assert frame.columns == schemas.column_names(name)


def test_every_input_row_is_accounted_for(
    synthetic_build: BuildResult, synthetic_snapshot: tuple[Path, RawManifest]
) -> None:
    snapshot, manifest = synthetic_snapshot
    by_file = {r["file"]: r for r in synthetic_build.manifest.reconciliation}
    assert set(by_file) == {f.path for f in manifest.files}
    for entry in manifest.files:
        text = (snapshot / entry.path).read_text(encoding="utf-8")
        independent = sum(1 for _ in csv.DictReader(io.StringIO(text)))
        row = by_file[entry.path]
        assert row["rows_in"] == independent == row["rows_canonical"] + row["rows_rejected"]
    reasons = set(synthetic_build.tables["rejected_rows"]["reason_code"].to_list())
    assert reasons <= set(schemas.REASON_CODES)


def test_expected_rejections(synthetic_build: BuildResult) -> None:
    rejected = synthetic_build.tables["rejected_rows"]
    tour = "atp/atp_matches_2023.csv"
    assert _rows(rejected, "INVALID_PLAYER_ID")[0] == (tour, 16)
    assert (tour, 17) in _rows(rejected, "SAME_PLAYER")
    assert (tour, 18) in _rows(rejected, "UNKNOWN_ROUND")
    assert (tour, 19) in _rows(rejected, "INVALID_TOURNEY_DATE")
    assert (tour, 20) in _rows(rejected, "INVALID_TOURNEY_ID")
    assert ("atp/atp_players.csv", 22) in _rows(rejected, "INVALID_PLAYER_ID")
    assert len(_rows(rejected, "PLAYER_CONFLICT")) == 2
    assert len(_rows(rejected, "RANKING_CONFLICT")) == 2
    assert _rows(rejected, "INVALID_RANKING_DATE") == [("atp/atp_rankings_20s.csv", 27)]
    assert _rows(rejected, "INVALID_RANK") == [("atp/atp_rankings_20s.csv", 28)]


# ----------------------------------------------------------------------------- AT-07 / AT-25


def test_match_keys_unique_and_collisions_resolved(synthetic_build: BuildResult) -> None:
    matches = synthetic_build.tables["matches"]
    assert matches["match_key"].n_unique() == matches.height
    rejected = synthetic_build.tables["rejected_rows"]
    # (a) exact duplicate within a file and across files: kept once.
    dups = _rows(rejected, "DUPLICATE_ROW")
    assert ("atp/atp_matches_2023.csv", 8) in dups
    assert ("atp/atp_matches_qual_chall_2023.csv", 1) in dups  # tour-file copy wins (priority)
    q1 = matches.filter(pl.col("round") == "Q1")
    assert q1.height == 1 and q1["source_file"][0] == "atp/atp_matches_2023.csv"
    # (b) distinct match_num -> two discriminated keys.
    rr = matches.filter(pl.col("tournament_id") == "sackmann:2023-0004")
    assert rr.height == 2 and set(rr["key_resolution"]) == {"match_num_discriminator"}
    assert set(rr["source_match_num"].to_list()) == {1, 2}
    # (c) same match_num -> both quarantined.
    collisions = rejected.filter(pl.col("reason_code") == "KEY_COLLISION")
    assert collisions.height == 2 and collisions["detail"].n_unique() == 1


def test_collision_resolution_independent_of_row_order() -> None:
    rows = [
        match_row(round="RR", tourney_level="F", match_num=n, winner_id=w, loser_id=lo)
        for n, w, lo in ((1, 115, 116), (2, 116, 115), (3, 115, 116))
    ]
    ref, chrono = load_reference(), ChronologyConfig()
    a, _, _ = build_matches(raw_match_frame(rows), ref, chrono)
    b, _, _ = build_matches(raw_match_frame(list(reversed(rows))), ref, chrono)
    key = ["match_key", "source_match_num", "player_a_id", "a_won"]
    assert (
        a["matches"]
        .select(key)
        .sort("match_key")
        .equals(b["matches"].select(key).sort("match_key"))
    )
    assert a["matches"].height == 3


# ----------------------------------------------------------------------------- AT-29


def test_padded_and_zero_prefixed_ids_canonicalise(synthetic_build: BuildResult) -> None:
    matches = synthetic_build.tables["matches"]
    sf = matches.filter(
        (pl.col("tournament_id") == "sackmann:2023-0001") & (pl.col("round") == "SF")
    )
    players = set(sf["player_a_id"].to_list() + sf["player_b_id"].to_list())
    assert {"sackmann:105", "sackmann:107"} <= players
    assert not any(p.startswith("sackmann:0") for p in players)


# ----------------------------------------------------------------------------- AT-10 / AT-19


def test_rebuild_is_deterministic(synthetic_build: BuildResult, tmp_path: Path) -> None:
    again = _rebuild(tmp_path, synthetic_files())
    assert again.build_id == synthetic_build.build_id
    assert _hashes(again) == _hashes(synthetic_build)


def _shuffle_csv(body: bytes, seed: int) -> bytes:
    lines = body.decode("utf-8").splitlines(keepends=True)
    header, rows = lines[0], lines[1:]
    random.Random(seed).shuffle(rows)
    return (header + "".join(rows)).encode("utf-8")


def test_row_order_does_not_change_content(synthetic_build: BuildResult, tmp_path: Path) -> None:
    files = {k: _shuffle_csv(v, seed=7) for k, v in synthetic_files().items()}
    shuffled = _rebuild(tmp_path, files)
    assert _hashes(shuffled) == _hashes(synthetic_build)


# ----------------------------------------------------------------------------- AT-26


def test_match_num_permutation_changes_only_the_hint(
    synthetic_build: BuildResult, tmp_path: Path
) -> None:
    files = synthetic_files()
    text = files["atp/atp_matches_2023.csv"].decode("utf-8")
    reader = list(csv.DictReader(io.StringIO(text)))
    qual = list(csv.DictReader(io.StringIO(files["atp/atp_matches_qual_chall_2023.csv"].decode())))

    def group(r: dict[str, str]) -> tuple[str, ...]:
        pair = sorted((r["winner_id"].strip().lstrip("0"), r["loser_id"].strip().lstrip("0")))
        return (r["tourney_id"], r["round"], *pair)

    counts: dict[tuple[str, ...], int] = {}
    for r in reader + qual:
        counts[group(r)] = counts.get(group(r), 0) + 1
    # Permute match_num only among rows that belong to no duplicate/collision group.
    for tid in ("2023-0001", "2023-0002"):
        idx = [i for i, r in enumerate(reader) if r["tourney_id"] == tid and counts[group(r)] == 1]
        nums = [reader[i]["match_num"] for i in idx]
        rotated = [str(500 + n) for n in range(len(nums))][::-1]
        for i, n in zip(idx, rotated, strict=True):
            reader[i]["match_num"] = n
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(reader[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(reader)
    files["atp/atp_matches_2023.csv"] = buffer.getvalue().encode("utf-8")
    permuted = _rebuild(tmp_path, files)

    ignore = {"source_match_num", "source_row_sha256"}
    cols = [c for c in schemas.column_names("matches") if c not in ignore]
    original = synthetic_build.tables["matches"].select(cols).sort("match_key")
    assert permuted.tables["matches"].select(cols).sort("match_key").equals(original)
    assert (
        permuted.tables["match_entry"]
        .sort("match_key", "player_id")
        .equals(synthetic_build.tables["match_entry"].sort("match_key", "player_id"))
    )


# ----------------------------------------------------------------------------- AT-11


def test_no_winner_first_columns_downstream(synthetic_build: BuildResult) -> None:
    for name, frame in synthetic_build.tables.items():
        for column in frame.columns:
            if column in schemas.ALLOWED_OUTCOME_COLUMNS:
                continue
            assert not column.startswith(schemas.FORBIDDEN_COLUMN_PREFIXES), (name, column)
    outcome_like = {
        "winner_id",
        "a_won",
        "outcome_type",
        "score_raw",
        "sets_a",
        "games_a",
        "minutes",
    }
    assert outcome_like <= schemas.post_match_columns("matches")


# ----------------------------------------------------------------------------- AT-14 / AT-15


def test_round_ordinals_and_precedence_conflicts() -> None:
    rows = [
        match_row(round="QF", match_num=1, winner_id=101, loser_id=102),
        match_row(round="QF", match_num=2, winner_id=101, loser_id=103),  # 101 twice in a QF
        match_row(round="SF", match_num=3, winner_id=104, loser_id=105),
    ]
    tables, _, _ = build_matches(raw_match_frame(rows), load_reference(), ChronologyConfig())
    m = tables["matches"]
    flagged = m.filter(pl.col("precedence_conflict"))
    assert flagged.height == 2 and set(flagged["round"]) == {"QF"}
    assert m.filter(pl.col("round") == "SF")["precedence_conflict"].to_list() == [False]


def test_chronology_fields(synthetic_build: BuildResult) -> None:
    m = synthetic_build.tables["matches"]
    assert m.filter(pl.col("match_day_lo") > pl.col("match_day_hi")).height == 0
    assert m.filter(pl.col("match_day_hi") > pl.col("tournament_end_bound")).height == 0
    assert m.filter(pl.col("prediction_cutoff_utc") >= pl.col("knowable_at_utc")).height == 0
    for lo, hi, cutoff, knowable in m.select(
        "match_day_lo", "match_day_hi", "prediction_cutoff_utc", "knowable_at_utc"
    ).iter_rows():
        assert cutoff == earliest_instant(lo) and knowable == latest_instant(hi)
    for col in ("match_date", "scheduled_start_utc", "completed_at_utc", "published_at_utc"):
        assert m[col].null_count() == m.height  # no source provides these yet
    r = synthetic_build.tables["rankings"]
    for d, k in r.select("ranking_date", "knowable_at_utc").iter_rows():
        assert k == latest_instant(d)


# ----------------------------------------------------------------------------- AT-18 / AT-20


def test_dataview_seals_holdout(synthetic_build: BuildResult, tmp_path: Path) -> None:
    view = DataView(synthetic_build.tables, SPLITS)
    dev = view.table("matches")
    assert dev["tournament_start_date"].max() <= date(2024, 12, 31)
    assert "sackmann:2025-0002" in set(dev["tournament_id"])  # starts 2024-12-30 -> development
    assert "sackmann:2025-0001" not in set(dev["tournament_id"])
    entry_keys = set(view.table("match_entry")["match_key"])
    assert entry_keys == set(dev["match_key"])
    assert view.table("rankings")["ranking_date"].max() <= date(2024, 12, 31)
    with pytest.raises(HoldoutAccessError):
        view.table("matches", end=date(2025, 1, 1))
    with pytest.raises(HoldoutAccessError):
        view.table("rejected_rows")
    with pytest.raises(HoldoutAccessError):
        DataView(synthetic_build.tables, SPLITS, mode="holdout")
    log = tmp_path / "holdout_access.log"
    sealed = DataView(synthetic_build.tables, SPLITS, mode="holdout", reason="test", access_log=log)
    assert sealed.table("matches").height == synthetic_build.tables["matches"].height
    assert "holdout\ttest" in log.read_text(encoding="utf-8")


def test_qa_report_holdout_section_is_integrity_only(synthetic_build: BuildResult) -> None:
    report = build_report(synthetic_build.tables, synthetic_build.out_dir, SPLITS)
    assert set(report["holdout_integrity_only"]) <= HOLDOUT_SECTION_KEYS
    assert report["holdout_integrity_only"]["matches"] == 1
    assert "orientation_checks" in report["development"]
    assert report["reconciliation_totals"]["rows_in"] == (
        report["reconciliation_totals"]["rows_canonical"]
        + report["reconciliation_totals"]["rows_rejected"]
    )


def test_licence_lineage_blocks_commercial_profile(synthetic_build: BuildResult) -> None:
    lineage = synthetic_build.manifest.licence_lineage
    assert [str(c) for c in lineage] == ["RESEARCH_NC"]
    assert set(synthetic_build.tables["matches"]["licence_class"]) == {"RESEARCH_NC"}
    with pytest.raises(LicenceError):
        assert_profile_allows(lineage, BuildProfile.COMMERCIAL)


# ----------------------------------------------------------------------------- tables content


def test_players_stats_and_rankings_content(synthetic_build: BuildResult) -> None:
    players = synthetic_build.tables["players"]
    p131 = players.filter(pl.col("player_id") == "sackmann:131").row(0, named=True)
    assert (
        p131["height_cm"] is None and p131["birth_date_precision"] == "year" and p131["hand"] == "U"
    )
    assert players.filter(pl.col("player_id") == "sackmann:130").height == 0
    stats = synthetic_build.tables["match_stats"]
    assert stats.filter(pl.col("return_points_won") < 0).height == 0
    assert synthetic_build.manifest.notes["stats_values_unparseable"] == 1
    ranks = synthetic_build.tables["rankings"]
    assert (
        ranks.filter(
            (pl.col("player_id") == "sackmann:101") & (pl.col("ranking_date") == date(2023, 1, 9))
        ).height
        == 0
    )  # conflicting rows quarantined
