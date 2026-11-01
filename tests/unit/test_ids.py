"""AT-24 key separation, AT-29 canonical player IDs, orientation independence."""

from __future__ import annotations

import hashlib
import itertools

import pytest

from quantedge.core.ids import (
    InvalidIdentifierError,
    canonical_player_id,
    canonical_tournament_id,
    discriminated_match_key,
    draw_category,
    match_key,
    orient,
)

CATEGORIES = ("tour_main", "tour_qualifying", "challenger_main", "challenger_qualifying")
BASE = {
    "tournament_id": "sackmann:2023-0001",
    "season": 2023,
    "draw_category": "tour_main",
    "round_code": "QF",
    "player_1": "sackmann:101",
    "player_2": "sackmann:102",
}


@pytest.mark.parametrize("raw", ["105", " 105", "105 ", "0105", "000105", 105])
def test_canonical_player_id_normalises(raw: str | int) -> None:
    assert canonical_player_id("sackmann", raw) == "sackmann:105"


@pytest.mark.parametrize("raw", ["12a", "", " ", "-5", "1.0", "1 2"])
def test_canonical_player_id_rejects_non_integers(raw: str) -> None:
    with pytest.raises(InvalidIdentifierError):
        canonical_player_id("sackmann", raw)


def test_tournament_id() -> None:
    assert canonical_tournament_id("sackmann", " 2019-560 ") == "sackmann:2019-560"
    with pytest.raises(InvalidIdentifierError):
        canonical_tournament_id("sackmann", "bad id")


def test_draw_category() -> None:
    assert draw_category("tour", "Q2") == "tour_qualifying"
    assert draw_category("challenger", "R32") == "challenger_main"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tournament_id", "sackmann:2023-0002"),
        ("season", 2024),
        ("draw_category", "tour_qualifying"),
        ("round_code", "SF"),
        ("player_2", "sackmann:103"),
    ],
)
def test_every_key_field_separates(field: str, value: object) -> None:
    changed = BASE | {field: value}
    assert match_key(**BASE) != match_key(**changed)  # type: ignore[arg-type]


def test_key_uses_unordered_pair() -> None:
    swapped = BASE | {"player_1": BASE["player_2"], "player_2": BASE["player_1"]}
    assert match_key(**BASE) == match_key(**swapped)  # type: ignore[arg-type]


def test_same_pair_across_seasons_and_draws_never_collides() -> None:
    keys = {
        match_key(**(BASE | {"season": s, "draw_category": d}))  # type: ignore[arg-type]
        for s, d in itertools.product(range(2000, 2027), CATEGORIES)
    }  # fmt: skip
    assert len(keys) == 27 * 4


def test_key_format_and_self_match_rejected() -> None:
    key = match_key(**BASE)  # type: ignore[arg-type]
    assert key.startswith("m1_") and len(key) == 35
    with pytest.raises(InvalidIdentifierError):
        match_key(**(BASE | {"player_2": BASE["player_1"]}))  # type: ignore[arg-type]


def test_discriminator_keys_distinct_and_stable() -> None:
    base = match_key(**BASE)  # type: ignore[arg-type]
    a, b = discriminated_match_key(base, 1), discriminated_match_key(base, 2)
    assert a != b != base and discriminated_match_key(base, 1) == a


def test_orientation_depends_only_on_key_and_unordered_pair() -> None:
    for i in range(200):
        key = match_key(**(BASE | {"player_2": f"sackmann:{1000 + i}"}))  # type: ignore[arg-type]
        p1, p2 = BASE["player_1"], f"sackmann:{1000 + i}"
        assert orient(key, p1, p2) == orient(key, p2, p1)
        assert set(orient(key, p1, p2)) == {p1, p2}


def _lp(*fields: str) -> bytes:
    return b"".join(str(len(f.encode())).encode() + b":" + f.encode() for f in fields)


def test_matches_independent_reimplementation_of_spec() -> None:
    """Re-derive key and orientation from the ARCHITECTURE.md §4.1-4.2 text with raw hashlib."""
    lo, hi = sorted((BASE["player_1"], BASE["player_2"]))
    payload = _lp("qe.match.v1", BASE["tournament_id"], "2023", "tour_main", "QF", lo, hi)
    expected_key = "m1_" + hashlib.sha256(payload).hexdigest()[:32]
    assert match_key(**BASE) == expected_key  # type: ignore[arg-type]
    bit = hashlib.sha256(_lp("qe.orient.v1", expected_key)).digest()[0] & 1
    assert orient(expected_key, hi, lo) == ((lo, hi) if bit == 0 else (hi, lo))
