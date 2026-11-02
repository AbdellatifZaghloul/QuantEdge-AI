"""Identity linking is scoped to the inner folds and curated aliases resolve (synthetic only)."""

from __future__ import annotations

from datetime import date

import polars as pl

from quantedge.core.splits import load_split_config
from quantedge.odds.commands import identity_view
from quantedge.odds.identity import PlayerResolver, load_player_aliases, normalize_name
from tests.conftest import REPO_ROOT

SPLITS = load_split_config(REPO_ROOT / "configs" / "splits.yaml")


def test_inner_end_is_last_inner_fold_day() -> None:
    assert SPLITS.inner_end == date(2022, 12, 31)
    assert SPLITS.inner_end < date(min(SPLITS.late_check_years), 1, 1)


def test_identity_view_never_returns_late_check_or_holdout_rows() -> None:
    days = [date(2022, 6, 27), date(2022, 12, 31), date(2023, 1, 16), date(2024, 6, 24)]
    tables = {
        "matches": pl.DataFrame(
            {
                "match_key": [f"m{i}" for i in range(len(days))],
                "tournament_start_date": days,
                "player_a_id": ["a"] * len(days),
                "player_b_id": ["b"] * len(days),
            }
        ),
        "tournaments": pl.DataFrame(
            {"tournament_id": ["t0", "t1", "t2", "t3"], "start_date": days}
        ),
    }
    matches, tournaments = identity_view(tables, SPLITS)
    assert matches["match_key"].to_list() == ["m0", "m1"]
    assert tournaments["tournament_id"].to_list() == ["t0", "t1"]


def test_curated_aliases_resolve_and_are_unique() -> None:
    aliases = {k[1]: v for k, v in load_player_aliases().items() if k[0] == "the_odds_api"}
    assert len(aliases) == 4 and all(v.startswith("sackmann:") for v in aliases.values())
    empty_players = pl.DataFrame(
        {"player_id": [], "name_first": [], "name_last": []},
        schema={"player_id": pl.String, "name_first": pl.String, "name_last": pl.String},
    )
    empty_apps = pl.DataFrame(
        {"player_id": [], "tournament_start_date": []},
        schema={"player_id": pl.String, "tournament_start_date": pl.Date},
    )
    resolver = PlayerResolver(empty_players, empty_apps, "the_odds_api")
    for name, expected in [
        ("Christopher O'Connell", "sackmann:106331"),
        ("Soonwoo Kwon", "sackmann:126952"),
        ("Albert Ramos-Vinolas", "sackmann:105077"),
        ("Holger Vitus Nodskov Rune", "sackmann:208029"),
    ]:
        result = resolver.resolve(name, at=None)
        assert (result.player_id, result.method) == (expected, "alias")
        assert normalize_name(name) in aliases
