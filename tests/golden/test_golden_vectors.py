"""AT-09: committed identity and orientation vectors reproduce exactly on Linux and Windows."""

from __future__ import annotations

import csv
from pathlib import Path

from quantedge.core.ids import discriminated_match_key, match_key, orient

VECTORS = Path(__file__).with_name("orientation_vectors.csv")


def test_golden_vectors_reproduce() -> None:
    with VECTORS.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) >= 50
    for row in rows:
        key = match_key(
            tournament_id=row["tournament_id"], season=int(row["season"]),
            draw_category=row["draw_category"], round_code=row["round"],
            player_1=row["player_1"], player_2=row["player_2"],
        )  # fmt: skip
        if row["match_num_discriminator"]:
            key = discriminated_match_key(key, int(row["match_num_discriminator"]))
        assert key == row["expected_match_key"], row
        a, _ = orient(key, row["player_1"], row["player_2"])
        assert a == row["expected_player_a"], row
        assert orient(key, row["player_2"], row["player_1"])[0] == a  # input order irrelevant
