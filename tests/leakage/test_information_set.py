"""AT-16 information-set scenarios and AT-27 point-in-time contract."""

from __future__ import annotations

from datetime import date

import polars as pl

from quantedge.canonical.build import BuildResult
from quantedge.core.asof import OUTCOME_FIELDS, admissible_results, rankings_as_of
from quantedge.core.chronology import earliest_instant, latest_instant
from tests.leakage.scenario import frame, mk

X, Y, Z, V, W = (f"sackmann:{i}" for i in (1, 2, 3, 4, 5))
T1, T2 = "sackmann:2023-0001", "sackmann:2023-0002"
D = date(2023, 1, 4)


def _admitted(frame_: pl.DataFrame, key: str) -> dict[str, str]:
    result = admissible_results(frame_, key)
    return dict(result.select("match_key", "admitted_via").iter_rows())


def test_a_same_day_matches_never_see_each_other() -> None:
    m1 = mk(T1, "QF", X, Y, D, D)
    m2 = mk(T2, "QF", Z, V, D, D)
    f = frame([m1, m2])
    assert _admitted(f, m1["match_key"]) == {}
    assert _admitted(f, m2["match_key"]) == {}


def test_b_own_previous_round_admitted_via_precedence_with_outcome_fields_only() -> None:
    qf = mk(T1, "QF", X, Y, D, date(2023, 1, 5))
    sf = mk(T1, "SF", X, Z, date(2023, 1, 5), date(2023, 1, 6))
    f = frame([qf, sf])
    assert qf["knowable_at_utc"] > sf["prediction_cutoff_utc"]  # not knowable by (K)
    result = admissible_results(f, sf["match_key"])
    assert dict(result.select("match_key", "admitted_via").iter_rows()) == {qf["match_key"]: "P"}
    p_rows = result.filter(pl.col("admitted_via") == "P")
    withheld = [c for c in result.columns if c not in {*OUTCOME_FIELDS, "admitted_via"}]
    assert "minutes" in withheld
    for column in withheld:  # post-match details beyond the outcome are withheld (null)
        assert p_rows[column].null_count() == p_rows.height, column
    assert p_rows["winner_id"].null_count() == 0


def test_c_other_players_same_round_results_excluded() -> None:
    qf_x = mk(T1, "QF", X, Y, D, date(2023, 1, 5))
    qf_other = mk(T1, "QF", V, W, D, date(2023, 1, 5))
    sf = mk(T1, "SF", X, Z, date(2023, 1, 5), date(2023, 1, 6))
    f = frame([qf_x, qf_other, sf])
    assert _admitted(f, sf["match_key"]) == {qf_x["match_key"]: "P"}


def test_d_overlapping_event_excluded_until_knowable() -> None:
    other_event = mk(T2, "F", X, V, date(2023, 1, 5), date(2023, 1, 6))
    sf = mk(T1, "SF", X, Z, date(2023, 1, 6), date(2023, 1, 7))
    f = frame([other_event, sf])
    assert other_event["knowable_at_utc"] > sf["prediction_cutoff_utc"]
    assert _admitted(f, sf["match_key"]) == {}
    later = mk(T1, "F", X, Y, date(2023, 1, 9), date(2023, 1, 9))
    f2 = frame([other_event, sf, later])
    assert _admitted(f2, later["match_key"])[other_event["match_key"]] == "K"


def test_e_round_robin_matches_have_no_mutual_precedence() -> None:
    rr1 = mk(T1, "RR", X, Y, D, date(2023, 1, 6))
    rr2 = mk(T1, "RR", X, Z, D, date(2023, 1, 6))
    sf = mk(T1, "SF", X, V, date(2023, 1, 6), date(2023, 1, 7))
    f = frame([rr1, rr2, sf])
    assert _admitted(f, rr2["match_key"]) == {}
    assert _admitted(f, sf["match_key"]) == {rr1["match_key"]: "P", rr2["match_key"]: "P"}


def test_f_ranking_dated_on_match_day_is_excluded() -> None:
    monday = date(2023, 1, 9)
    rankings = pl.DataFrame(
        {
            "player_id": [X, X],
            "ranking_date": [date(2023, 1, 2), monday],
            "rank": [10, 5],
            "knowable_at_utc": [latest_instant(date(2023, 1, 2)), latest_instant(monday)],
        }
    )
    cutoff = earliest_instant(monday)
    visible = rankings_as_of(rankings, cutoff)
    assert visible["ranking_date"].to_list() == [date(2023, 1, 2)]


def test_conflicted_or_ineligible_rounds_get_no_precedence() -> None:
    qf = mk(T1, "QF", X, Y, D, date(2023, 1, 5), conflict=True)
    sf = mk(T1, "SF", X, Z, date(2023, 1, 5), date(2023, 1, 6))
    assert _admitted(frame([qf, sf]), sf["match_key"]) == {}
    er = mk(T1, "ER", X, Y, D, date(2023, 1, 5))
    assert _admitted(frame([er, sf]), sf["match_key"]) == {}


def test_k_admits_settled_history() -> None:
    old = mk(T2, "F", Y, Z, date(2022, 12, 1), date(2022, 12, 4))
    sf = mk(T1, "SF", X, Z, date(2023, 1, 5), date(2023, 1, 6))
    assert _admitted(frame([old, sf]), sf["match_key"]) == {old["match_key"]: "K"}


def test_at27_contract_on_synthetic_build(synthetic_build: BuildResult) -> None:
    matches = synthetic_build.tables["matches"]
    for target in matches.iter_rows(named=True):
        admitted = admissible_results(matches, target["match_key"])
        k_rows = admitted.filter(pl.col("admitted_via") == "K")
        assert (
            k_rows.filter(pl.col("knowable_at_utc") > target["prediction_cutoff_utc"]).height == 0
        )
        p_rows = admitted.filter(pl.col("admitted_via") == "P")
        if p_rows.height:
            assert set(p_rows["tournament_id"]) == {target["tournament_id"]}
            assert p_rows.filter(pl.col("round_ordinal") >= target["round_ordinal"]).height == 0
            participants = {target["player_a_id"], target["player_b_id"]}
            for a, b in p_rows.select("player_a_id", "player_b_id").iter_rows():
                assert {a, b} & participants
        assert target["match_key"] not in set(admitted["match_key"])
