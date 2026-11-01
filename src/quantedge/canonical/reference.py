"""Committed reference tables (rounds, levels, event classes, day bounds, score tokens).

The CSVs live in ``quantedge/reference`` and are hand-curated with a rationale column. They contain
no licensed rows. Their combined hash is part of every build ID.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import cache
from importlib import resources

from quantedge.core.hashing import sha256_fields

REFERENCE_FILES = (
    "level_codes.csv",
    "round_day_bounds.csv",
    "round_ordinal.csv",
    "score_tokens.csv",
    "tournament_classes.csv",
)

OUTCOME_TYPES = ("completed", "retired", "walkover", "default", "abandoned", "unknown")
WILDCARD = "*"


class ReferenceError(ValueError):
    """Raised when a reference table is internally inconsistent."""


@dataclass(frozen=True)
class RoundInfo:
    code: str
    ordinal: int
    stage: str
    precedence_eligible: bool


@dataclass(frozen=True)
class EventClassRule:
    level: str
    min_draw_size: int
    event_class: str
    nominal_days: int
    end_offset_days: int


@dataclass(frozen=True)
class Reference:
    rounds: dict[str, RoundInfo]
    level_tiers: dict[str, str]
    class_rules: tuple[EventClassRule, ...]
    day_bounds: dict[tuple[str, str], tuple[int, int]]
    score_tokens: dict[str, str]
    fingerprint: str

    def event_class(self, level: str | None, draw_size: int | None) -> EventClassRule:
        """First matching rule for the level with ``draw_size >= min_draw_size``.

        A missing draw size only matches rules with ``min_draw_size == 0``; anything unmatched
        falls back to the wildcard (widest) class.
        """
        for rule in self.class_rules:
            if rule.level != level:
                continue
            if draw_size is None:
                if rule.min_draw_size == 0:
                    return rule
                continue
            if draw_size >= rule.min_draw_size:
                return rule
        return self.wildcard_class

    @property
    def wildcard_class(self) -> EventClassRule:
        for rule in self.class_rules:
            if rule.level == WILDCARD:
                return rule
        raise ReferenceError("tournament_classes.csv lacks a wildcard row")

    def bounds(self, event_class: str, round_code: str) -> tuple[int, int]:
        key = (event_class, round_code)
        if key in self.day_bounds:
            return self.day_bounds[key]
        return self.day_bounds[(event_class, WILDCARD)]

    def tier(self, level: str | None) -> str:
        return self.level_tiers.get(level or "", "other")


def _read(name: str) -> tuple[str, list[dict[str, str]]]:
    text = resources.files("quantedge.reference").joinpath(name).read_text(encoding="utf-8")
    return text, list(csv.DictReader(text.splitlines()))


def _bool(value: str) -> bool:
    if value not in {"true", "false"}:
        raise ReferenceError(f"expected true/false, got {value!r}")
    return value == "true"


@cache
def load_reference() -> Reference:
    texts: list[str] = []

    text, rows = _read("round_ordinal.csv")
    texts.append(text)
    rounds = {
        r["round"]: RoundInfo(
            r["round"], int(r["ordinal"]), r["stage"], _bool(r["precedence_eligible"])
        )
        for r in rows
    }

    text, rows = _read("level_codes.csv")
    texts.append(text)
    tiers = {r["level"]: r["tier"] for r in rows}

    text, rows = _read("tournament_classes.csv")
    texts.append(text)
    class_rules = tuple(
        EventClassRule(
            r["level"],
            int(r["min_draw_size"]),
            r["event_class"],
            int(r["nominal_days"]),
            int(r["end_offset_days"]),
        )
        for r in rows
    )

    text, rows = _read("round_day_bounds.csv")
    texts.append(text)
    bounds = {(r["event_class"], r["round"]): (int(r["lo"]), int(r["hi"])) for r in rows}

    text, rows = _read("score_tokens.csv")
    texts.append(text)
    tokens = {r["token"]: r["outcome_type"] for r in rows}

    reference = Reference(
        rounds=rounds,
        level_tiers=tiers,
        class_rules=class_rules,
        day_bounds=bounds,
        score_tokens=tokens,
        fingerprint=sha256_fields("qe.reference.v1", *texts),
    )
    validate_reference(reference)
    return reference


def validate_reference(ref: Reference) -> None:
    """Structural invariants every reference revision must satisfy."""
    classes = {rule.event_class for rule in ref.class_rules}
    for event_class in classes:
        if (event_class, WILDCARD) not in ref.day_bounds:
            raise ReferenceError(f"event class {event_class!r} has no wildcard day bounds")
    end_offsets = {rule.event_class: rule.end_offset_days for rule in ref.class_rules}
    for (event_class, round_code), (lo, hi) in ref.day_bounds.items():
        if event_class not in classes:
            raise ReferenceError(f"day bounds reference unknown class {event_class!r}")
        if round_code != WILDCARD and round_code not in ref.rounds:
            raise ReferenceError(f"day bounds reference unknown round {round_code!r}")
        if lo > hi:
            raise ReferenceError(f"lo > hi for {(event_class, round_code)}")
        if hi > end_offsets[event_class]:
            raise ReferenceError(f"hi exceeds end bound for {(event_class, round_code)}")
    for outcome in ref.score_tokens.values():
        if outcome not in OUTCOME_TYPES:
            raise ReferenceError(f"unknown outcome type {outcome!r}")
    if not any(rule.level == WILDCARD for rule in ref.class_rules):
        raise ReferenceError("tournament_classes.csv lacks a wildcard row")
