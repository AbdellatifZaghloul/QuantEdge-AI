"""Elo and surface Elo state for the point-in-time replay engine.

Formulas (docs/reviews/phase-2.md §3):

- Expected score: ``E_a = 1 / (1 + 10 ** ((R_b - R_a) / scale))``.
- Update: ``R_a <- R_a + K_a (S_a - E_a)`` with per-player K (not forced zero-sum).
- K: ``constant`` -> ``K = k_constant``; ``fivethirtyeight`` -> ``K = c / (n + offset) ** shape``,
  where ``n`` is the player's number of prior rated matches on that track.
- Optional decay toward the initial rating with half-life ``h`` days after a grace period ``g``,
  applied lazily: ``R(t) = R0 + (R - R0) * 0.5 ** (max(0, days_since_last - g) / h)``.
  ``g = 0`` is continuous shrinkage; ``g > 0`` penalises inactivity only.
- Surface tracks are separate Elo ratings on matches of one surface, starting at the initial rating.
  Partial pooling happens at prediction time (``pooled_surface_rating``).

All updates of one knowability batch are computed from the pre-batch state, so the result does not
depend on the order of results inside a batch.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from quantedge.core.asof import Result, Target

SURFACES = ("Hard", "Clay", "Grass", "Carpet")


class EloConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_rating: float = 1500.0
    scale: float = 400.0
    k_mode: Literal["constant", "fivethirtyeight"] = "fivethirtyeight"
    k_constant: float = 32.0
    k_c: float = 250.0
    k_offset: float = 5.0
    k_shape: float = 0.4
    decay_half_life_days: float | None = None
    decay_grace_days: float = 0.0

    @model_validator(mode="after")
    def _positive(self) -> EloConfig:
        if self.scale <= 0 or self.k_constant <= 0 or self.k_c <= 0 or self.k_offset <= 0:
            raise ValueError("scale and K parameters must be positive")
        if self.decay_half_life_days is not None and self.decay_half_life_days <= 0:
            raise ValueError("decay half-life must be positive")
        if self.decay_grace_days < 0:
            raise ValueError("decay grace period must be non-negative")
        return self

    def label(self) -> str:
        k = (
            f"K{self.k_constant:g}"
            if self.k_mode == "constant"
            else f"538c{self.k_c:g}o{self.k_offset:g}s{self.k_shape:g}"
        )
        decay = (
            f"_h{self.decay_half_life_days:g}g{self.decay_grace_days:g}"
            if self.decay_half_life_days
            else ""
        )
        return k + decay


def expected_score(r_a: float, r_b: float, scale: float = 400.0) -> float:
    return float(1.0 / (1.0 + 10.0 ** ((r_b - r_a) / scale)))


def k_factor(n_prior: int, config: EloConfig) -> float:
    if config.k_mode == "constant":
        return config.k_constant
    return float(config.k_c / (n_prior + config.k_offset) ** config.k_shape)


@dataclass(frozen=True, slots=True)
class Track:
    rating: float
    n: int
    last: datetime | None


def decayed_rating(track: Track, at: datetime, config: EloConfig) -> float:
    h = config.decay_half_life_days
    if h is None or track.last is None:
        return track.rating
    days = max((at - track.last).total_seconds() / 86_400.0 - config.decay_grace_days, 0.0)
    return float(config.initial_rating + (track.rating - config.initial_rating) * 0.5 ** (days / h))


class EloState:
    """Overall and per-surface tracks. ``fork()`` returns a copy-on-write overlay."""

    __slots__ = ("_base", "config", "overall", "surface")

    def __init__(self, config: EloConfig, base: EloState | None = None) -> None:
        self.config = config
        self._base = base
        self.overall: dict[str, Track] = {}
        self.surface: dict[tuple[str, str], Track] = {}

    def _default(self) -> Track:
        return Track(self.config.initial_rating, 0, None)

    def overall_of(self, player: str) -> Track:
        track = self.overall.get(player)
        if track is not None:
            return track
        return self._base.overall_of(player) if self._base is not None else self._default()

    def surface_of(self, player: str, surface: str) -> Track:
        track = self.surface.get((player, surface))
        if track is not None:
            return track
        return self._base.surface_of(player, surface) if self._base is not None else self._default()

    def fork(self) -> EloState:
        return EloState(self.config, base=self)

    def rated_players(self) -> set[str]:
        players = set(self.overall)
        if self._base is not None:
            players |= self._base.rated_players()
        return players


def _batch_deltas(
    pairs: Sequence[tuple[Result, Track, Track]], config: EloConfig
) -> dict[int, tuple[float, float]]:
    out: dict[int, tuple[float, float]] = {}
    for i, (r, track_a, track_b) in enumerate(pairs):
        when = r.event_time_utc or r.knowable_at_utc
        ra = decayed_rating(track_a, when, config)
        rb = decayed_rating(track_b, when, config)
        e_a = expected_score(ra, rb, config.scale)
        s_a = 1.0 if r.winner_id == r.player_a_id else 0.0
        out[i] = (
            k_factor(track_a.n, config) * (s_a - e_a),
            k_factor(track_b.n, config) * ((1.0 - s_a) - (1.0 - e_a)),
        )
    return out


def _apply(
    pre: dict[Any, Track],
    deltas: dict[Any, float],
    counts: dict[Any, int],
    last: dict[Any, datetime],
    config: EloConfig,
    target: dict[Any, Track],
) -> None:
    for key, delta in deltas.items():
        track = pre[key]
        base = decayed_rating(track, last[key], config)
        target[key] = Track(base + delta, track.n + counts[key], last[key])


def apply_batch(state: EloState, batch: Sequence[Result]) -> EloState:
    """Apply one knowability batch simultaneously (all deltas from pre-batch ratings)."""
    config = state.config
    tracks = [(r, state.overall_of(r.player_a_id), state.overall_of(r.player_b_id)) for r in batch]
    deltas = _batch_deltas(tracks, config)
    pre: dict[Any, Track] = {}
    acc: dict[Any, float] = {}
    counts: dict[Any, int] = {}
    last: dict[Any, datetime] = {}
    for i, (r, track_a, track_b) in enumerate(tracks):
        when = r.event_time_utc or r.knowable_at_utc
        for player, track, delta in (
            (r.player_a_id, track_a, deltas[i][0]),
            (r.player_b_id, track_b, deltas[i][1]),
        ):
            pre.setdefault(player, track)
            acc[player] = acc.get(player, 0.0) + delta
            counts[player] = counts.get(player, 0) + 1
            last[player] = max(last.get(player, when), when)
    _apply(pre, acc, counts, last, config, state.overall)

    surface_rows = [r for r in batch if r.surface in SURFACES]
    s_tracks = [
        (
            r,
            state.surface_of(r.player_a_id, r.surface or ""),
            state.surface_of(r.player_b_id, r.surface or ""),
        )
        for r in surface_rows
    ]
    s_deltas = _batch_deltas(s_tracks, config)
    s_pre: dict[Any, Track] = {}
    s_acc: dict[Any, float] = {}
    s_counts: dict[Any, int] = {}
    s_last: dict[Any, datetime] = {}
    for i, (r, track_a, track_b) in enumerate(s_tracks):
        when = r.event_time_utc or r.knowable_at_utc
        for player, track, delta in (
            (r.player_a_id, track_a, s_deltas[i][0]),
            (r.player_b_id, track_b, s_deltas[i][1]),
        ):
            key = (player, r.surface)
            s_pre.setdefault(key, track)
            s_acc[key] = s_acc.get(key, 0.0) + delta
            s_counts[key] = s_counts.get(key, 0) + 1
            s_last[key] = max(s_last.get(key, when), when)
    _apply(s_pre, s_acc, s_counts, s_last, config, state.surface)
    return state


def read_features(state: EloState, target: Target) -> Mapping[str, Any]:
    """Pre-match ratings for both players at the target's cutoff."""
    config = state.config
    at = target.prediction_cutoff_utc
    out: dict[str, Any] = {}
    for side, player in (("a", target.player_a_id), ("b", target.player_b_id)):
        track = state.overall_of(player)
        out[f"elo_{side}"] = decayed_rating(track, at, config)
        out[f"elo_n_{side}"] = track.n
        out[f"days_since_last_{side}"] = (
            None if track.last is None else (at - track.last).total_seconds() / 86_400.0
        )
        if target.surface in SURFACES:
            s_track = state.surface_of(player, target.surface or "")
            out[f"surface_elo_{side}"] = decayed_rating(s_track, at, config)
            out[f"surface_n_{side}"] = s_track.n
        else:
            out[f"surface_elo_{side}"] = out[f"elo_{side}"]
            out[f"surface_n_{side}"] = 0
    return out


def pooled_surface_rating(
    surface_rating: float, surface_n: int, overall_rating: float, pseudo_count: float
) -> float:
    """Partial pooling: ``w = n / (n + m)``; ``m = 0`` -> pure surface, ``m -> inf`` -> overall."""
    if math.isinf(pseudo_count):
        return overall_rating
    if pseudo_count == 0:
        return surface_rating
    w = surface_n / (surface_n + pseudo_count)
    return w * surface_rating + (1.0 - w) * overall_rating
