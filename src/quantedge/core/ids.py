"""Stable identifiers and outcome-independent orientation (ARCHITECTURE.md §4.1-4.2, D-14).

No function in this module accepts an outcome field. Identity and orientation are functions of the
unordered canonical player pair and the event context only.
"""

from __future__ import annotations

import re

from quantedge.core.hashing import sha256_fields

SACKMANN_NS = "sackmann"

MATCH_KEY_DOMAIN = "qe.match.v1"
MATCH_DISCRIMINATOR_DOMAIN = "qe.match.discriminator.v1"
ORIENTATION_DOMAIN = "qe.orient.v1"
MATCH_KEY_PREFIX = "m1_"
_KEY_HEX_CHARS = 32  # 16 bytes

_INTEGER_ID = re.compile(r"^[0-9]+$")
_NAMESPACE = re.compile(r"^[a-z][a-z0-9_]*$")
_TOURNEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-./]*$")

DRAW_STAGES = ("main", "qualifying")


class InvalidIdentifierError(ValueError):
    """Raised when a source identifier cannot be canonicalised."""


def _check_namespace(namespace: str) -> None:
    if not _NAMESPACE.match(namespace):
        raise InvalidIdentifierError(f"invalid namespace {namespace!r}")


def canonical_player_id(namespace: str, raw: str | int) -> str:
    """``"<ns>:<int>"``; strips whitespace and leading zeros, rejects non-integers."""
    _check_namespace(namespace)
    text = str(raw).strip()
    if not _INTEGER_ID.match(text):
        raise InvalidIdentifierError(f"player id {raw!r} is not a non-negative integer")
    return f"{namespace}:{int(text)}"


def canonical_tournament_id(namespace: str, raw: str) -> str:
    _check_namespace(namespace)
    text = raw.strip()
    if not _TOURNEY_ID.match(text):
        raise InvalidIdentifierError(f"tournament id {raw!r} is not a valid token")
    return f"{namespace}:{text}"


def draw_category(tier: str, round_code: str) -> str:
    """``<tier>_<stage>`` where stage is ``qualifying`` for Q-rounds, else ``main``."""
    stage = "qualifying" if round_code.startswith("Q") else "main"
    return f"{tier}_{stage}"


def match_key(
    *,
    tournament_id: str,
    season: int,
    draw_category: str,
    round_code: str,
    player_1: str,
    player_2: str,
) -> str:
    """Base match key over event context and the *unordered* canonical player pair."""
    if player_1 == player_2:
        raise InvalidIdentifierError("a match needs two distinct players")
    low, high = sorted((player_1, player_2))
    digest = sha256_fields(
        MATCH_KEY_DOMAIN, tournament_id, str(season), draw_category, round_code, low, high
    )
    return MATCH_KEY_PREFIX + digest[:_KEY_HEX_CHARS]


def discriminated_match_key(base_key: str, match_num: int) -> str:
    """Key for one member of a collision group whose members all have distinct ``match_num``."""
    digest = sha256_fields(MATCH_DISCRIMINATOR_DOMAIN, base_key, "match_num", str(match_num))
    return MATCH_KEY_PREFIX + digest[:_KEY_HEX_CHARS]


def orientation_bit(key: str) -> int:
    return int(sha256_fields(ORIENTATION_DOMAIN, key)[:2], 16) & 1


def orient(key: str, player_1: str, player_2: str) -> tuple[str, str]:
    """Return ``(player_a, player_b)``; depends only on the key and the unordered pair."""
    if player_1 == player_2:
        raise InvalidIdentifierError("a match needs two distinct players")
    low, high = sorted((player_1, player_2))
    return (low, high) if orientation_bit(key) == 0 else (high, low)
