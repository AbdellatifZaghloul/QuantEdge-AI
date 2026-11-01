"""Property tests: AT-08 winner-swap invariance (>= 1,000 generated rows), AT-13 score round-trip,
AT-24 key separation."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from quantedge.canonical import schemas
from quantedge.canonical.build import RAW_MATCH_COLUMNS, build_matches
from quantedge.canonical.reference import load_reference
from quantedge.canonical.score import SetScore, format_score, parse_score
from quantedge.core.chronology import ChronologyConfig
from quantedge.core.ids import match_key
from tests.fixtures.synthetic import match_row, raw_match_frame

REF = load_reference()
CHRONO = ChronologyConfig()
ROUNDS = sorted(REF.rounds)
LEVELS = ["G", "M", "A", "C", "D", "F", "O", "S", "Z"]

ids = st.integers(min_value=1, max_value=999_999)


@st.composite
def raw_rows(draw: st.DrawFn) -> list[dict[str, str]]:
    n = draw(st.integers(min_value=4, max_value=8))
    rows = []
    used: set[tuple[str, str, frozenset[int]]] = set()
    for i in range(n):
        w = draw(ids)
        lo = draw(ids.filter(lambda x, w=w: x != w))
        tid = f"2023-{draw(st.integers(1, 3)):04d}"
        rnd = draw(st.sampled_from(ROUNDS))
        if (tid, rnd, frozenset((w, lo))) in used:
            continue
        used.add((tid, rnd, frozenset((w, lo))))
        rows.append(
            match_row(
                tourney_id=tid, tourney_level=draw(st.sampled_from(LEVELS)),
                draw_size=draw(st.sampled_from(["", "4", "32", "128"])),
                tourney_date=draw(st.sampled_from(["20230102", "20230116", "20231030"])),
                round=rnd, match_num=i + 1, winner_id=w, loser_id=lo,
                winner_rank=draw(st.integers(1, 2000)), loser_rank=draw(st.integers(1, 2000)),
                score=draw(st.sampled_from(["6-4 6-4", "7-6(3) 4-6 6-1", "W/O", "6-3 1-0 RET"])),
            )
        )  # fmt: skip
    return rows


def _swap(row: dict[str, str]) -> dict[str, str]:
    out = dict(row)
    for col in RAW_MATCH_COLUMNS:
        for a, b in (("winner_", "loser_"), ("w_", "l_")):
            if col.startswith(a):
                other = b + col[len(a) :]
                out[col], out[other] = row[other], row[col]
    return out


PRE_MATCH = [
    c for c in schemas.column_names("matches")
    if c not in schemas.post_match_columns("matches") and c != "source_row_sha256"
]  # fmt: skip


@settings(
    max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)  # >= 1,200 rows
@given(raw_rows())
def test_winner_swap_invariance(rows: list[dict[str, str]]) -> None:
    original, _, _ = build_matches(raw_match_frame(rows), REF, CHRONO)
    swapped, _, _ = build_matches(raw_match_frame([_swap(r) for r in rows]), REF, CHRONO)
    a = original["matches"].sort("match_key")
    b = swapped["matches"].sort("match_key")
    assert a.select(PRE_MATCH).equals(b.select(PRE_MATCH))
    assert (a["a_won"] != b["a_won"]).all()
    entry_cols = ["match_key", "player_id", "side", "rank_at_event_start"]
    assert (
        original["match_entry"]
        .select(entry_cols)
        .sort("match_key", "player_id")
        .equals(swapped["match_entry"].select(entry_cols).sort("match_key", "player_id"))
    )


# ----------------------------------------------------------------------------- AT-13 round trip


@st.composite
def completed_scores(draw: st.DrawFn) -> tuple[tuple[SetScore, ...], int]:
    best_of = draw(st.sampled_from([3, 5]))
    needed = best_of // 2 + 1
    lost = draw(st.integers(0, needed - 1))
    order = [*draw(st.permutations([True] * (needed - 1) + [False] * lost)), True]
    sets = []
    for match_winner_takes_set in order:
        kind = draw(st.sampled_from(["regular", "seven_five", "tiebreak", "advantage"]))
        if kind == "regular":
            hi, lo, tb = 6, draw(st.integers(0, 4)), None
        elif kind == "seven_five":
            hi, lo, tb = 7, 5, None
        elif kind == "tiebreak":
            hi, lo, tb = 7, 6, draw(st.integers(0, 20))
        else:
            lo = draw(st.integers(6, 30))
            hi, tb = lo + 2, None
        w, l_ = (hi, lo) if match_winner_takes_set else (lo, hi)
        sets.append(SetScore(w, l_, tb))
    return tuple(sets), best_of


@settings(max_examples=400, deadline=None)
@given(completed_scores())
def test_completed_scores_round_trip(case: tuple[tuple[SetScore, ...], int]) -> None:
    sets, best_of = case
    parsed = parse_score(format_score(sets), best_of)
    assert parsed.sets == sets
    assert (parsed.outcome_type, parsed.score_status) == ("completed", "ok")
    assert parsed.sets_won_by_winner == best_of // 2 + 1


@settings(max_examples=200, deadline=None)
@given(completed_scores(), st.sampled_from(["RET", "DEF", "W/O"]))
def test_terminal_tokens_never_raise(case: tuple[tuple[SetScore, ...], int], token: str) -> None:
    sets, best_of = case
    parsed = parse_score(format_score(sets[:-1], token), best_of)
    assert parsed.outcome_type in {"retired", "default", "walkover"}


@given(st.text(max_size=40))
def test_parser_total_on_arbitrary_text(text: str) -> None:
    parse_score(text, 3)  # must never raise


# ----------------------------------------------------------------------------- AT-24


@given(
    st.text(min_size=1, max_size=12), st.integers(1968, 2030),
    st.sampled_from(["tour_main", "tour_qualifying"]),
    st.sampled_from(ROUNDS), ids, ids,
)  # fmt: skip
def test_key_changes_when_any_field_changes(
    tid: str, season: int, draw_cat: str, rnd: str, p1: int, p2: int
) -> None:
    if p1 == p2:
        return
    args = {"tournament_id": tid, "season": season, "draw_category": draw_cat,
            "round_code": rnd, "player_1": f"x:{p1}", "player_2": f"x:{p2}"}  # fmt: skip
    base = match_key(**args)  # type: ignore[arg-type]
    assert match_key(**(args | {"season": season + 1})) != base  # type: ignore[arg-type]
    assert match_key(**(args | {"tournament_id": tid + "x"})) != base  # type: ignore[arg-type]
    other_cat = "tour_main" if draw_cat != "tour_main" else "tour_qualifying"
    assert match_key(**(args | {"draw_category": other_cat})) != base  # type: ignore[arg-type]
