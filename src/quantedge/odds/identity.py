"""Player identity reconciliation and event linking for external odds.

Principles:
- Deterministic rules only. Approximate (fuzzy) string similarity is used to *suggest* aliases for
  human review, never to accept a match.
- An event is ``linked`` only if both players resolve unambiguously, exactly one canonical match
  exists for the unordered pair, the provider start time lies within that match's conservative
  day-bound window, the tournament is consistent with a curated alias, and no other provider event
  claims the same match. Every other outcome keeps an explicit status and reason.
"""

from __future__ import annotations

import csv
import difflib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from functools import cache
from importlib import resources

import polars as pl

_SPACE = re.compile(r"\s+")
_DASHES_QUOTES = "".join(chr(c) for c in (*range(0x2010, 0x2016), 0x2019))
_PUNCT = re.compile("[" + re.escape("-'`.,") + _DASHES_QUOTES + "]")


def normalize_name(name: str) -> str:
    """Accent-free, lower-case, punctuation-free, single-spaced name."""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = _PUNCT.sub(" ", ascii_only.lower())
    return _SPACE.sub(" ", cleaned).strip()


def name_keys(first: str | None, last: str | None) -> dict[str, str]:
    """Lookup keys for a canonical player: full name and reversed (surname-first) order."""
    first_n = normalize_name(first or "")
    last_n = normalize_name(last or "")
    keys = {}
    if first_n and last_n:
        keys["full"] = f"{first_n} {last_n}"
        keys["reversed"] = f"{last_n} {first_n}"
    elif last_n:
        keys["last_only"] = last_n
    return keys


@dataclass(frozen=True)
class Resolution:
    player_id: str | None
    method: str  # alias | exact_name | exact_name_active | ambiguous | unmatched
    candidates: tuple[str, ...]
    suggestions: tuple[str, ...] = ()


@cache
def load_player_aliases() -> dict[tuple[str, str], str]:
    text = (
        resources.files("quantedge.reference")
        .joinpath("player_name_aliases.csv")
        .read_text(encoding="utf-8")
    )
    return {
        (r["provider"], normalize_name(r["provider_name"])): r["player_id"]
        for r in csv.DictReader(text.splitlines())
    }


@cache
def load_tournament_aliases() -> dict[tuple[str, str], tuple[str, frozenset[str]]]:
    text = (
        resources.files("quantedge.reference")
        .joinpath("odds_tournament_aliases.csv")
        .read_text(encoding="utf-8")
    )
    return {
        (r["provider"], r["competition_key"]): (
            r["canonical_name_regex"],
            frozenset(x for x in r["canonical_levels"].split("|") if x),
        )
        for r in csv.DictReader(text.splitlines())
    }


class PlayerResolver:
    """Resolve provider player names to canonical ``player_id``s."""

    def __init__(self, players: pl.DataFrame, appearances: pl.DataFrame, provider: str) -> None:
        """``appearances``: (player_id, tournament_start_date) rows from canonical matches."""
        self.provider = provider
        self.index: dict[str, set[str]] = defaultdict(set)
        for pid, first, last in players.select("player_id", "name_first", "name_last").iter_rows():
            for kind, key in name_keys(first, last).items():
                if kind != "last_only":
                    self.index[key].add(pid)
        self.dates: dict[str, list] = defaultdict(list)  # type: ignore[type-arg]
        for pid, day in appearances.select("player_id", "tournament_start_date").iter_rows():
            self.dates[pid].append(day)
        self.aliases = {k[1]: v for k, v in load_player_aliases().items() if k[0] == provider}

    def _active(self, pid: str, before: object, window_days: int = 400) -> bool:
        assert hasattr(before, "date")
        cutoff = before.date()
        return any(
            cutoff - timedelta(days=window_days) <= d <= cutoff for d in self.dates.get(pid, [])
        )

    def resolve(self, name: str, *, at: object) -> Resolution:
        key = normalize_name(name)
        if key in self.aliases:
            return Resolution(self.aliases[key], "alias", (self.aliases[key],))
        candidates = tuple(sorted(self.index.get(key, set())))
        if len(candidates) == 1:
            return Resolution(candidates[0], "exact_name", candidates)
        if len(candidates) > 1:
            # Same printed name for several players: use only activity *before* the event.
            active = tuple(c for c in candidates if self._active(c, at))
            if len(active) == 1:
                return Resolution(active[0], "exact_name_active", candidates)
            return Resolution(None, "ambiguous", candidates)
        suggestions = tuple(difflib.get_close_matches(key, list(self.index), n=3, cutoff=0.85))
        return Resolution(None, "unmatched", (), suggestions)


def link_events(
    events: pl.DataFrame,
    matches: pl.DataFrame,
    tournaments: pl.DataFrame,
    resolver: PlayerResolver,
    *,
    provider: str,
) -> pl.DataFrame:
    """Link provider events to canonical matches; returns one ``event_links`` row per event."""
    aliases = load_tournament_aliases()
    t_meta = {
        tid: (name or "", level or "")
        for tid, name, level in tournaments.select("tournament_id", "name", "level").iter_rows()
    }
    by_pair: dict[frozenset[str], list[dict[str, object]]] = defaultdict(list)
    for m in matches.select(
        "match_key",
        "tournament_id",
        "player_a_id",
        "player_b_id",
        "match_day_lo",
        "match_day_hi",
        "prediction_cutoff_utc",
        "knowable_at_utc",
    ).iter_rows(named=True):
        by_pair[frozenset((m["player_a_id"], m["player_b_id"]))].append(m)

    rows = []
    for ev in events.select(
        "event_id", "competition_key", "participant_1", "participant_2", "commence_time_utc"
    ).iter_rows(named=True):
        at = ev["commence_time_utc"]
        r1 = resolver.resolve(ev["participant_1"], at=at)
        r2 = resolver.resolve(ev["participant_2"], at=at)
        row: dict[str, object] = {
            "event_id": ev["event_id"],
            "match_key": None,
            "player_1_id": r1.player_id,
            "player_2_id": r2.player_id,
            "player_1_method": r1.method,
            "player_2_method": r2.method,
            "participant_1_side": None,
            "candidate_count": 0,
            "commence_within_day_bounds": None,
        }
        if r1.player_id is None or r2.player_id is None or r1.player_id == r2.player_id:
            detail = f"p1={r1.method}{list(r1.suggestions)} p2={r2.method}{list(r2.suggestions)}"
            rows.append(row | {"status": "player_unresolved", "reason": detail})
            continue
        pair = by_pair.get(frozenset((r1.player_id, r2.player_id)), [])
        row["candidate_count"] = len(pair)
        timely = [m for m in pair if m["prediction_cutoff_utc"] <= at <= m["knowable_at_utc"]]
        alias = aliases.get((provider, ev["competition_key"]))
        if not pair:
            rows.append(row | {"status": "unmatched", "reason": "no canonical match for this pair"})
            continue
        if not timely:
            rows.append(
                row
                | {
                    "status": "time_inconsistent",
                    "reason": "start time outside every candidate's day-bound window",
                }
            )
            continue
        if len(timely) > 1:
            rows.append(
                row | {"status": "ambiguous", "reason": f"{len(timely)} time-consistent candidates"}
            )
            continue
        match = timely[0]
        name, level = t_meta.get(str(match["tournament_id"]), ("", ""))
        if alias is None:
            rows.append(
                row
                | {
                    "status": "tournament_unverified",
                    "reason": f"no curated alias for {ev['competition_key']}",
                }
            )
            continue
        pattern, levels = alias
        if not re.search(pattern, name) or (levels and level not in levels):
            rows.append(
                row
                | {
                    "status": "tournament_unverified",
                    "reason": f"canonical tournament {name!r}/{level} does not match alias",
                }
            )
            continue
        side = "A" if match["player_a_id"] == r1.player_id else "B"
        lo, hi = match["match_day_lo"], match["match_day_hi"]
        rows.append(
            row
            | {
                "status": "linked",
                "reason": "unique pair, time and tournament consistent",
                "match_key": match["match_key"],
                "participant_1_side": side,
                "commence_within_day_bounds": lo <= at.date() <= hi,
            }
        )
    links = pl.DataFrame(rows, schema_overrides={"candidate_count": pl.Int32}) if rows else None
    if links is None:
        from quantedge.odds.schemas import empty_table  # noqa: PLC0415

        return empty_table("event_links")
    # Two provider events claiming the same match: neither is trusted.
    claimed = (
        links.filter(pl.col("status") == "linked")
        .group_by("match_key")
        .len()
        .filter(pl.col("len") > 1)
    )
    dup = pl.col("match_key").is_in(claimed["match_key"].to_list()) & (pl.col("status") == "linked")
    return links.with_columns(
        pl.when(dup)
        .then(pl.lit("duplicate_provider_event"))
        .otherwise(pl.col("status"))
        .alias("status"),
        pl.when(dup)
        .then(pl.lit("several provider events link to one match"))
        .otherwise(pl.col("reason"))
        .alias("reason"),
    ).sort("event_id")
