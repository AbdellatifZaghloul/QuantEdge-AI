"""AT-13: score parser on hand-written cases (>= 40)."""

from __future__ import annotations

import pytest

from quantedge.canonical.score import parse_score

# (raw, best_of, outcome_type, score_status, sets_won_by_winner, sets_won_by_loser)
CASES: list[tuple[str | None, int | None, str, str, int, int]] = [
    ("6-4 6-4", 3, "completed", "ok", 2, 0),
    ("6-4 3-6 6-3", 3, "completed", "ok", 2, 1),
    ("7-6(5) 6-4", 3, "completed", "ok", 2, 0),
    ("6-7(5) 7-6(3) 6-4", 3, "completed", "ok", 2, 1),
    ("7-6 6-4", 3, "completed", "ok", 2, 0),
    ("6-4 6-4 6-4", 5, "completed", "ok", 3, 0),
    ("4-6 6-4 6-4 3-6 6-4", 5, "completed", "ok", 3, 2),
    ("6-4 3-6 7-6(8)", 3, "completed", "ok", 2, 1),
    ("6-4 4-6 [10-8]", 3, "completed", "ok", 2, 1),
    ("4-6 6-3 [11-9]", 3, "completed", "ok", 2, 1),
    ("6-3 2-1 RET", 3, "retired", "ok", 1, 0),
    ("RET", 3, "retired", "ok", 0, 0),
    ("Ret'd", 3, "retired", "ok", 0, 0),
    ("W/O", 3, "walkover", "ok", 0, 0),
    ("Walkover", 5, "walkover", "ok", 0, 0),
    ("6-4 W/O", 3, "walkover", "inconsistent", 1, 0),
    ("6-4 DEF", 3, "default", "ok", 1, 0),
    ("Def.", 3, "default", "ok", 0, 0),
    ("Default", 3, "default", "ok", 0, 0),
    ("6-4 2-2 ABD", 3, "abandoned", "ok", 1, 0),
    ("ABN", 3, "abandoned", "ok", 0, 0),
    ("6-1 5-4 Played and unfinished", 5, "abandoned", "ok", 1, 0),
    ("2-2 Played and abandoned", 5, "abandoned", "ok", 0, 0),
    ("6-3 unfinished", 3, "abandoned", "ok", 1, 0),
    ("UNK", 3, "unknown", "missing", 0, 0),
    ("NA", 3, "unknown", "missing", 0, 0),
    ("UNP", 3, "unknown", "missing", 0, 0),
    (None, 3, "unknown", "missing", 0, 0),
    ("", 3, "unknown", "missing", 0, 0),
    ("   ", 3, "unknown", "missing", 0, 0),
    ("6-36-3", 3, "unknown", "malformed", 0, 0),
    (">", 3, "unknown", "malformed", 0, 0),
    ("RE", 3, "unknown", "malformed", 0, 0),
    ("6-4 ?-?", 3, "unknown", "uncertain", 1, 0),
    ("7-5 6-2 6-1?", 3, "unknown", "uncertain", 2, 0),
    ("6-4 6-4", 5, "unknown", "inconsistent", 2, 0),
    ("6-3", 3, "unknown", "inconsistent", 1, 0),
    ("4-6 6-3 [7-10]", 3, "unknown", "inconsistent", 1, 2),
    ("6-2 8-9 6-2", 3, "completed", "ok", 2, 1),
    ("8-9 9-8 6-3 6-2", 5, "completed", "ok", 3, 1),
    ("4-3(5) 4-1 4-2", 5, "completed", "ok", 3, 0),
    ("4-1 3-4(5) 4-1 4-2", 5, "completed", "ok", 3, 1),
    ("6-8 11-9 6-2 17-19 6-4", 5, "completed", "ok", 3, 2),
    ("31-24", 1, "completed", "ok", 1, 0),
    ("6-5", 1, "unknown", "inconsistent", 0, 0),
    ("6-4 6-4", None, "completed", "ok", 2, 0),
    ("6-4  6-4", 3, "completed", "ok", 2, 0),
    ("6-4 3-6 6-4 4-6 13-12(5)", 5, "completed", "ok", 3, 2),
    ("6-0(3) 6-3", 3, "unknown", "inconsistent", 1, 0),
    ("7-6(10) 7-6(12)", 3, "completed", "ok", 2, 0),
]


def test_case_count() -> None:
    assert len(CASES) >= 40


@pytest.mark.parametrize(("raw", "best_of", "outcome", "status", "won", "lost"), CASES)
def test_parse_cases(
    raw: str | None, best_of: int | None, outcome: str, status: str, won: int, lost: int
) -> None:
    parsed = parse_score(raw, best_of)
    assert (parsed.outcome_type, parsed.score_status) == (outcome, status)
    assert (parsed.sets_won_by_winner, parsed.sets_won_by_loser) == (won, lost)


def test_games_and_tiebreak_counts() -> None:
    p = parse_score("6-7(5) 7-6(3) 6-4", 3)
    assert (p.games_winner, p.games_loser, p.tiebreak_sets, p.has_match_tiebreak) == (
        19,
        17,
        2,
        False,
    )
    p = parse_score("6-4 4-6 [10-8]", 3)
    # A match tiebreak counts as one game for its winner.
    assert (p.games_winner, p.games_loser, p.has_match_tiebreak) == (11, 10, True)
    p = parse_score("6-3 2-1 RET", 3)
    assert (p.games_winner, p.games_loser) == (8, 4)  # unfinished-set games still count as played


def test_unknown_tokens_reported_not_raised() -> None:
    p = parse_score("6-4 6-3 >?", 3)
    assert p.outcome_type == "unknown"
    p = parse_score("6-4 xyz 6-3", 3)
    assert p.score_status == "malformed" and p.unknown_tokens == ("xyz",)
