"""Score parsing for Sackmann-style score strings.

Conventions of the source:
- Set tokens are written from the **match winner's** perspective: ``4-6`` means the match
  winner lost that set 4-6.
- ``7-6(5)``: a tiebreak set; the parenthesised number is the tiebreak loser's points.
- ``[10-8]``: a match tiebreak played instead of a deciding set.
- Terminal tokens (``RET``, ``W/O``, ``DEF``, ...) come from ``reference/score_tokens.csv``.

The parser never raises on data. Anything it cannot interpret yields ``outcome_type="unknown"``
and a non-``ok`` ``score_status``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from quantedge.canonical.reference import load_reference

_SET = re.compile(r"^(\d{1,2})-(\d{1,2})(?:\((\d{1,2})\))?$")
_MATCH_TB = re.compile(r"^\[(\d{1,2})-(\d{1,2})\]$")

SCORE_STATUSES = ("ok", "inconsistent", "uncertain", "malformed", "missing")


@dataclass(frozen=True)
class SetScore:
    """Games from the match winner's (``w``) and match loser's (``l``) perspective."""

    w: int
    l: int  # noqa: E741 - mirrors the source convention
    tiebreak_loser_points: int | None = None
    is_match_tiebreak: bool = False

    @property
    def winner_won_set(self) -> bool | None:
        if self.w == self.l:
            return None
        return self.w > self.l

    def is_complete(self, *, short_format: bool) -> bool:
        hi, lo = max(self.w, self.l), min(self.w, self.l)
        if self.is_match_tiebreak:
            return (hi, lo) == (1, 0) or (hi >= 7 and hi - lo >= 2)
        if short_format:
            return hi == 4 and (lo <= 2 or (lo == 3 and self.tiebreak_loser_points is not None))
        if self.tiebreak_loser_points is not None:
            return hi == lo + 1 and lo >= 6
        # 7-6, 9-8 (1970s nine-point tiebreak), 13-12 etc. are tiebreak sets written without points.
        return (hi >= 6 and hi - lo >= 2) or (hi == lo + 1 and lo >= 6)

    @property
    def is_tiebreak_set(self) -> bool:
        hi, lo = max(self.w, self.l), min(self.w, self.l)
        return not self.is_match_tiebreak and (
            self.tiebreak_loser_points is not None or (hi == lo + 1 and lo >= 6)
        )

    def format(self) -> str:
        if self.is_match_tiebreak:
            return f"[{self.w}-{self.l}]"
        suffix = f"({self.tiebreak_loser_points})" if self.tiebreak_loser_points is not None else ""
        return f"{self.w}-{self.l}{suffix}"


@dataclass(frozen=True)
class ParsedScore:
    outcome_type: str
    score_status: str
    sets: tuple[SetScore, ...] = ()
    terminal_token: str | None = None
    unknown_tokens: tuple[str, ...] = field(default_factory=tuple)

    @property
    def short_format(self) -> bool:
        """First-to-four sets (e.g. Next Gen Finals): every regular set has at most four games."""
        regular = [s for s in self.sets if not s.is_match_tiebreak]
        return bool(regular) and max(max(s.w, s.l) for s in regular) <= 4

    def _complete_sets(self) -> list[SetScore]:
        return [s for s in self.sets if s.is_complete(short_format=self.short_format)]

    @property
    def sets_won_by_winner(self) -> int:
        """Completed sets won by the match winner (an unfinished set never counts)."""
        return sum(1 for s in self._complete_sets() if s.winner_won_set is True)

    @property
    def sets_won_by_loser(self) -> int:
        return sum(1 for s in self._complete_sets() if s.winner_won_set is False)

    @property
    def games_winner(self) -> int:
        """Games won by the match winner; a match tiebreak counts as one game for its winner."""
        total = 0
        for s in self.sets:
            total += (1 if s.w > s.l else 0) if s.is_match_tiebreak else s.w
        return total

    @property
    def games_loser(self) -> int:
        total = 0
        for s in self.sets:
            total += (1 if s.l > s.w else 0) if s.is_match_tiebreak else s.l
        return total

    @property
    def tiebreak_sets(self) -> int:
        return sum(1 for s in self.sets if s.is_tiebreak_set)

    @property
    def has_match_tiebreak(self) -> bool:
        return any(s.is_match_tiebreak for s in self.sets)


def format_score(sets: tuple[SetScore, ...], terminal_token: str | None = None) -> str:
    parts = [s.format() for s in sets]
    if terminal_token:
        parts.append(terminal_token)
    return " ".join(parts)


def _strip_terminal(text: str, tokens: dict[str, str]) -> tuple[str, str | None]:
    for token in sorted(tokens, key=len, reverse=True):
        if text == token:
            return "", token
        if text.endswith(" " + token):
            return text[: -len(token) - 1].rstrip(), token
    return text, None


def parse_score(raw: str | None, best_of: int | None = None) -> ParsedScore:
    tokens_map = load_reference().score_tokens
    if raw is None or not raw.strip():
        return ParsedScore("unknown", "missing")
    text = " ".join(raw.split())
    remainder, terminal = _strip_terminal(text, tokens_map)
    terminal_outcome = tokens_map[terminal] if terminal else None

    sets: list[SetScore] = []
    unknown: list[str] = []
    uncertain = False
    for token in remainder.split(" ") if remainder else []:
        if (m := _SET.match(token)) is not None:
            tb = int(m.group(3)) if m.group(3) is not None else None
            sets.append(SetScore(int(m.group(1)), int(m.group(2)), tb))
        elif (m := _MATCH_TB.match(token)) is not None:
            sets.append(SetScore(int(m.group(1)), int(m.group(2)), None, is_match_tiebreak=True))
        elif "?" in token:
            uncertain = True
        else:
            unknown.append(token)
    found = tuple(sets)

    if unknown:
        return ParsedScore("unknown", "malformed", found, terminal, tuple(unknown))
    if uncertain:
        return ParsedScore("unknown", "uncertain", found, terminal)
    if terminal_outcome == "unknown":
        return ParsedScore("unknown", "missing", found, terminal)
    if terminal_outcome == "walkover":
        status = "ok" if not found else "inconsistent"
        return ParsedScore("walkover", status, found, terminal)
    if terminal_outcome in {"retired", "default", "abandoned"}:
        return ParsedScore(terminal_outcome, "ok", found, terminal)

    # No terminal token: the match should be complete and won by the recorded winner.
    if not found:
        return ParsedScore("unknown", "malformed", found, None)
    parsed = ParsedScore("completed", "ok", found, None)
    complete = all(s.is_complete(short_format=parsed.short_format) for s in found)
    won, lost = parsed.sets_won_by_winner, parsed.sets_won_by_loser
    if best_of is not None and best_of > 0:
        needed = best_of // 2 + 1
        consistent = won == needed and lost < needed and won + lost == len(found)
    else:
        consistent = won > lost and won + lost == len(found)
    if complete and consistent:
        return parsed
    return ParsedScore("unknown", "inconsistent", found, None)
