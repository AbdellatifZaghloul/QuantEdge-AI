"""Gate 0: every anomaly policy on synthetic rows, determinism, and no silent contamination."""

from __future__ import annotations

import polars as pl
import pytest

from quantedge.canonical.build import build_matches
from quantedge.canonical.reference import load_reference
from quantedge.core.chronology import ChronologyConfig, latest_instant
from quantedge.core.hashing import frame_content_hash
from quantedge.curation.eligibility import ALL_REASONS, build_eligibility, score_subclass
from tests.fixtures.synthetic import match_row, raw_match_frame

E = {"tourney_id": "2023-0100", "tourney_name": "Synthetic Policy Open", "draw_size": "32"}
TEAM = {"tourney_id": "2023-0200", "tourney_name": "United Cup", "draw_size": "18"}


def rows() -> list[dict[str, str]]:
    r = [
        match_row(**E, round="R32", match_num=1, winner_id=301, loser_id=302),  # OK
        match_row(**E, round="R32", match_num=2, winner_id=303, loser_id=199999),  # placeholder
        match_row(**E, round="R32", match_num=3, winner_id=304, loser_id=305, score="W/O"),
        match_row(**E, round="R32", match_num=4, winner_id=306, loser_id=307, score="DEF"),
        match_row(**E, round="R32", match_num=5, winner_id=308, loser_id=309, score="6-3 2-1 DEF"),
        match_row(**E, round="R32", match_num=6, winner_id=310, loser_id=311, score="6-3 2-1 RET"),
        match_row(**E, round="R32", match_num=7, winner_id=312, loser_id=313, score="6-4 2-2 ABD"),
        match_row(**E, round="R32", match_num=8, winner_id=314, loser_id=315, score="UNK"),
        match_row(**E, round="R32", match_num=9, winner_id=316, loser_id=317, score="6-4 ?-?"),
        match_row(**E, round="R32", match_num=10, winner_id=318, loser_id=319, score="6-36-3"),
        match_row(
            **E, round="R32", match_num=11, winner_id=320, loser_id=321, score="6-4 6-4 6-4"
        ),  # bo3 3-0
        match_row(**E, round="R32", match_num=12, winner_id=322, loser_id=323, score="6-3 1-0"),
        match_row(
            **E, round="R32", match_num=13, winner_id=324, loser_id=325, score="4-6 6-3 [7-10]"
        ),
        # same pair twice in a knockout round, same winner -> one kept
        match_row(**E, round="R16", match_num=20, winner_id=301, loser_id=326, score="6-4 6-4"),
        match_row(**E, round="R16", match_num=21, winner_id=301, loser_id=326, score="6-4 6-4"),
        # same pair twice, reversed winners -> both excluded
        match_row(**E, round="R16", match_num=22, winner_id=327, loser_id=328),
        match_row(**E, round="R16", match_num=23, winner_id=328, loser_id=327),
        # 329 twice in one round, different opponents -> round label conflict (kept, deferred)
        match_row(**E, round="R16", match_num=24, winner_id=329, loser_id=330),
        match_row(**E, round="R16", match_num=25, winner_id=331, loser_id=329),
        # knockout contradiction: 332 loses in QF but appears in SF
        match_row(**E, round="QF", match_num=30, winner_id=333, loser_id=332),
        match_row(**E, round="SF", match_num=31, winner_id=332, loser_id=334),
        # team event: losing a tie rubber and playing later is legitimate
        match_row(**TEAM, round="QF", match_num=1, winner_id=340, loser_id=341),
        match_row(**TEAM, round="SF", match_num=2, winner_id=341, loser_id=342),
    ]
    return r


@pytest.fixture(scope="module")
def tables() -> dict[str, pl.DataFrame]:
    built, _, _ = build_matches(raw_match_frame(rows()), load_reference(), ChronologyConfig())
    return built


@pytest.fixture(scope="module")
def elig(tables: dict[str, pl.DataFrame]) -> pl.DataFrame:
    return build_eligibility(tables["matches"], tables["tournaments"]).join(
        tables["matches"].select(
            "match_key",
            "source_match_num",
            "tournament_id",
            "tournament_end_bound",
            "knowable_at_utc",
        ),
        on="match_key",
    )


def reason(elig: pl.DataFrame, tid: str, num: int) -> dict[str, object]:
    return elig.filter(
        (pl.col("tournament_id") == f"sackmann:{tid}") & (pl.col("source_match_num") == num)
    ).row(0, named=True)


@pytest.mark.parametrize(
    ("num", "expected", "update", "retire_update", "evaluate"),
    [
        (1, "OK", True, True, True),
        (2, "PLACEHOLDER_PLAYER", False, False, False),
        (3, "WALKOVER", False, False, False),
        (4, "DEFAULT_UNPLAYED", False, False, False),
        (5, "DEFAULT_PLAYED", False, True, False),
        (6, "RETIRED", False, True, False),
        (7, "ABANDONED", False, False, False),
        (8, "SCORE_MISSING", False, False, False),
        (9, "SCORE_UNCERTAIN", False, False, False),
        (10, "SCORE_MALFORMED", False, False, False),
        (11, "SCORE_FORMAT_MISMATCH", True, True, True),
        (12, "INCOMPLETE_UNTERMINATED", False, True, False),
        (13, "SCORE_CONTRADICTION", False, False, False),
        (22, "CONTRADICTORY_DUPLICATE", False, False, False),
        (23, "CONTRADICTORY_DUPLICATE", False, False, False),
        (24, "ROUND_LABEL_CONFLICT", True, True, False),
        (25, "ROUND_LABEL_CONFLICT", True, True, False),
        (30, "KNOCKOUT_CONTRADICTION", False, False, False),
        (31, "KNOCKOUT_CONTRADICTION", False, False, False),
    ],
)
def test_policies(
    elig: pl.DataFrame, num: int, expected: str, update: bool, retire_update: bool, evaluate: bool
) -> None:
    r = reason(elig, "2023-0100", num)
    assert r["primary_reason"] == expected
    assert (r["elo_update_baseline"], r["elo_update_retirements"], r["eval_eligible"]) == (
        update,
        retire_update,
        evaluate,
    )


def test_duplicates_keep_exactly_one(elig: pl.DataFrame) -> None:
    pair = [reason(elig, "2023-0100", n) for n in (20, 21)]
    assert sorted(r["primary_reason"] for r in pair) == ["DUPLICATE_RECORD", "OK"]
    kept = next(r for r in pair if r["primary_reason"] == "OK")
    assert kept["match_key"] == min(r["match_key"] for r in pair)  # deterministic choice


def test_round_label_conflict_defers_knowability(elig: pl.DataFrame) -> None:
    r = reason(elig, "2023-0100", 24)
    assert r["knowability_deferred"] and not r["precedence_allowed"]
    assert r["effective_knowable_at_utc"] == latest_instant(r["tournament_end_bound"])
    assert r["effective_knowable_at_utc"] > r["knowable_at_utc"]
    ok = reason(elig, "2023-0100", 1)
    assert ok["effective_knowable_at_utc"] == ok["knowable_at_utc"]


def test_team_event_is_not_a_contradiction(elig: pl.DataFrame) -> None:
    for num in (1, 2):
        r = reason(elig, "2023-0200", num)
        assert r["team_event"] and r["primary_reason"] == "OK"


def test_every_reason_is_known_and_population_is_clean(elig: pl.DataFrame) -> None:
    assert set(elig["primary_reason"]) <= set(ALL_REASONS)
    evaluated = elig.filter(pl.col("eval_eligible"))
    assert set(evaluated["primary_reason"]) <= {"OK", "SCORE_FORMAT_MISMATCH"}
    assert (
        not evaluated.select(
            pl.any_horizontal(
                "placeholder_player",
                "duplicate_record",
                "contradictory_duplicate",
                "round_label_conflict",
                "knockout_contradiction",
            )
        )
        .to_series()
        .any()
    )
    updates = elig.filter(pl.col("elo_update_baseline"))
    assert not updates["placeholder_player"].any()


def test_eligibility_is_deterministic_and_row_order_free() -> None:
    a, _, _ = build_matches(raw_match_frame(rows()), load_reference(), ChronologyConfig())
    b, _, _ = build_matches(
        raw_match_frame(list(reversed(rows()))), load_reference(), ChronologyConfig()
    )
    ea = build_eligibility(a["matches"], a["tournaments"])
    eb = build_eligibility(b["matches"], b["tournaments"])
    assert frame_content_hash(ea) == frame_content_hash(eb)


def test_score_subclasses() -> None:
    assert score_subclass("6-4 6-4 6-4", 3) == "complete_winner_majority_bestof_mismatch"
    assert score_subclass("6-3 1-0", 3) == "incomplete_final_set_no_terminal"
    assert score_subclass("6-5 6-4", 3) == "incomplete_nonfinal_set"
    assert score_subclass("6-7 6-3 4-6", 3) == "winner_fewer_sets"
    assert score_subclass("2-18 21-15", 3) == "sets_tied"
    assert score_subclass("6-4 6-4", 3) == "not_inconsistent"
