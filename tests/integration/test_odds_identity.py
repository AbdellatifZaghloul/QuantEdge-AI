"""Player resolution and event linking: never accept an uncertain match silently."""

from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl
import pytest

from quantedge.odds.identity import PlayerResolver, link_events, normalize_name
from quantedge.odds.tables import event_id
from tests.leakage.scenario import frame, mk

PLAYERS = pl.DataFrame(
    {
        "player_id": [
            "sackmann:801",
            "sackmann:802",
            "sackmann:803",
            "sackmann:804",
            "sackmann:805",
            "sackmann:806",
        ],
        "name_first": ["Alex", "Alex", "José", "Jean-Luc", "Bruno", "Carl"],
        "name_last": ["Synth", "Synth", "Pérez", "Tester", "Doe", "Roe"],
    }
)
WIMBLEDON = "sackmann:2024-540"
T = pl.DataFrame(
    {
        "tournament_id": [WIMBLEDON, "sackmann:2024-999"],
        "name": ["Wimbledon", "Doha"],
        "level": ["G", "A"],
    }
)


def appearances(*rows: tuple[str, date]) -> pl.DataFrame:
    return pl.DataFrame(
        {"player_id": [r[0] for r in rows], "tournament_start_date": [r[1] for r in rows]}
    )


def resolver(*rows: tuple[str, date]) -> PlayerResolver:
    return PlayerResolver(
        PLAYERS,
        appearances(*rows) if rows else appearances(("x", date(2000, 1, 1))),
        "the_odds_api",
    )


def at(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def test_normalize_name() -> None:
    assert normalize_name("  José  PÉREZ ") == "jose perez"
    assert normalize_name("Jean-Luc O'Tester") == "jean luc o tester"


def test_resolution_rules() -> None:
    r = resolver(("sackmann:801", date(2024, 5, 1)))
    assert r.resolve("Jose Perez", at=at(date(2024, 7, 1))).player_id == "sackmann:803"
    assert r.resolve("Perez Jose", at=at(date(2024, 7, 1))).method == "exact_name"  # surname first
    assert r.resolve("Jean Luc Tester", at=at(date(2024, 7, 1))).player_id == "sackmann:804"
    same_name = r.resolve("Alex Synth", at=at(date(2024, 7, 1)))
    assert (same_name.player_id, same_name.method) == ("sackmann:801", "exact_name_active")
    # Activity *after* the event must not disambiguate.
    future_only = resolver(("sackmann:802", date(2025, 1, 1)))
    assert future_only.resolve("Alex Synth", at=at(date(2024, 7, 1))).method == "ambiguous"
    typo = r.resolve("Jose Pereza", at=at(date(2024, 7, 1)))
    assert (
        typo.player_id is None and typo.method == "unmatched" and typo.suggestions
    )  # suggested only


def odds_events(*rows: tuple[str, str, str, datetime]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "event_id": [event_id("the_odds_api", r[0]) for r in rows],
            "competition_key": [r[3].tzname() and "tennis_atp_wimbledon" for r in rows],
            "participant_1": [r[1] for r in rows],
            "participant_2": [r[2] for r in rows],
            "commence_time_utc": [r[3] for r in rows],
        }
    )


@pytest.fixture
def matches() -> pl.DataFrame:
    d = date(2024, 7, 1)
    return frame(
        [
            mk(WIMBLEDON, "R128", "sackmann:803", "sackmann:804", d, date(2024, 7, 5)),
            mk(
                WIMBLEDON, "R64", "sackmann:805", "sackmann:806", date(2024, 7, 3), date(2024, 7, 7)
            ),
            mk("sackmann:2024-999", "R32", "sackmann:805", "sackmann:803", d, date(2024, 7, 3)),
        ]
    ).with_columns(pl.col("match_day_lo").alias("tournament_start_date"))


def link(
    evts: pl.DataFrame, matches: pl.DataFrame, r: PlayerResolver | None = None
) -> pl.DataFrame:
    return link_events(evts, matches, T, r or resolver(), provider="the_odds_api")


def test_linked_event_and_side_mapping(matches: pl.DataFrame) -> None:
    out = link(odds_events(("e1", "Jean-Luc Tester", "José Pérez", at(date(2024, 7, 2)))), matches)
    row = out.row(0, named=True)
    assert row["status"] == "linked" and row["commence_within_day_bounds"]
    m = matches.filter(pl.col("match_key") == row["match_key"]).row(0, named=True)
    expected = "A" if m["player_a_id"] == "sackmann:804" else "B"
    assert row["participant_1_side"] == expected


def test_time_inconsistent_unmatched_and_unresolved(matches: pl.DataFrame) -> None:
    out = link(
        odds_events(
            ("late", "Jean-Luc Tester", "José Pérez", at(date(2024, 7, 20))),
            ("nopair", "Jean-Luc Tester", "Carl Roe", at(date(2024, 7, 2))),
            ("ghost", "Nobody Known", "José Pérez", at(date(2024, 7, 2))),
            ("twin", "Alex Synth", "José Pérez", at(date(2024, 7, 2))),
        ),
        matches,
    )
    status = dict(zip(out["event_id"], out["status"], strict=True))
    assert status[event_id("the_odds_api", "late")] == "time_inconsistent"
    assert status[event_id("the_odds_api", "nopair")] == "unmatched"
    assert status[event_id("the_odds_api", "ghost")] == "player_unresolved"
    assert status[event_id("the_odds_api", "twin")] == "player_unresolved"  # ambiguous name
    assert out.filter(pl.col("status") != "linked")["match_key"].null_count() == out.height


def test_tournament_alias_must_match(matches: pl.DataFrame) -> None:
    # Bruno Doe vs José Pérez only exists at "Doha"; a Wimbledon-keyed event must not link to it.
    out = link(odds_events(("e", "Bruno Doe", "José Pérez", at(date(2024, 7, 2)))), matches)
    assert out["status"].to_list() == ["tournament_unverified"]


def test_ambiguous_candidates_and_duplicate_provider_events(matches: pl.DataFrame) -> None:
    rematch = mk(
        WIMBLEDON, "QF", "sackmann:803", "sackmann:804", date(2024, 7, 2), date(2024, 7, 9)
    )
    doubled = pl.concat(
        [
            matches,
            frame([rematch]).with_columns(pl.col("match_day_lo").alias("tournament_start_date")),
        ],
        how="diagonal_relaxed",
    )
    out = link(odds_events(("e", "Jean-Luc Tester", "José Pérez", at(date(2024, 7, 3)))), doubled)
    assert out["status"].to_list() == ["ambiguous"]
    dup = link(
        odds_events(
            ("e1", "Bruno Doe", "Carl Roe", at(date(2024, 7, 4))),
            ("e2", "Carl Roe", "Bruno Doe", at(date(2024, 7, 4))),
        ),
        matches,
    )
    assert dup["status"].to_list() == ["duplicate_provider_event"] * 2
