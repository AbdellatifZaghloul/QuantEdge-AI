"""Point-in-time features for the Phase 5 gradient-boosting candidate.

Every feature of target match *m* uses only information available at ``prediction_cutoff_utc(m)``:

- Elo / surface Elo / days since last match: the Phase 2 replay (knowability rule plus the
  earlier-round exception, outcome fields only);
- point-in-time ranks: ``rankings`` rows with ``knowable_at_utc <= cutoff`` (Phase 2 helper);
- serve/return statistics, Elo-residual form and workload: a **knowability-only** sweep. A past
  result contributes only once ``knowable_at_utc <= cutoff`` (match statistics never use the
  earlier-round exception, as required by ``core/asof.py``);
- age at event start, round, best-of, level, surface, draw category: known before the match.

Player-specific features enter as A - B differences (antisymmetric under swapping the players);
context features are shared by both players (symmetric).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl

from quantedge.models.rank_baseline import point_in_time_ranks
from quantedge.ratings.elo import EloConfig
from quantedge.ratings.features import elo_features

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
SURFACES = ("Hard", "Clay", "Grass", "Carpet")

# Antisymmetric (A - B) and symmetric (shared) model inputs. Order is part of the model contract.
DIFF_FEATURES = (
    "elo_diff",
    "surface_elo_diff",
    "log_elo_n_diff",
    "log_rank_diff",
    "unranked_diff",
    "serve_diff",
    "return_diff",
    "surface_serve_diff",
    "surface_return_diff",
    "bp_saved_diff",
    "log_stats_points_diff",
    "form_diff",
    "matches_14_diff",
    "matches_30_diff",
    "minutes_14_diff",
    "minutes_30_diff",
    "days_since_last_diff",
    "inactive_diff",
    "age_diff",
)
SHARED_FEATURES = (
    "elo_mean",
    "log_elo_n_min",
    "best_of",
    "level_code",
    "surface_code",
    "draw_code",
    "round_ordinal",
    "rounds_into_draw",
)
CATEGORICAL = ("level_code", "surface_code", "draw_code")


@dataclass(frozen=True)
class FeatureParams:
    elo_reference: dict[str, Any]
    rank_staleness_days: int = 365
    rank_cap: int = 2000
    stats_half_life_days: float = 365.0
    stats_prior_points: float = 200.0
    surface_stats_prior_points: float = 400.0
    form_half_life_days: float = 90.0
    workload_windows_days: tuple[int, int] = (14, 30)
    inactivity_days: float = 60.0


def _days(ts: datetime) -> float:
    return (ts - EPOCH).total_seconds() / 86_400.0


class _Decayed:
    """Exponentially decayed sums kept in growth form: value(T) = s * exp(-lam * T)."""

    __slots__ = ("lam", "sums")

    def __init__(self, half_life_days: float, size: int) -> None:
        self.lam = math.log(2.0) / half_life_days
        self.sums = np.zeros(size)

    def add(self, t: float, values: np.ndarray) -> None:
        self.sums += values * math.exp(self.lam * t)

    def at(self, t: float) -> np.ndarray:
        out: np.ndarray = self.sums * math.exp(-self.lam * t)
        return out


@dataclass
class _Player:
    stats: _Decayed  # serve points, serve won, return points, return won, bp faced, bp saved
    surface: dict[str, _Decayed]
    form: _Decayed  # residual sum, weight
    recent: deque[tuple[float, float]]  # (event day, minutes or nan)


def _stats_rows(match_stats: pl.DataFrame) -> dict[tuple[str, str], np.ndarray]:
    cols = (
        pl.col("serve_points").cast(pl.Float64),
        (pl.col("first_serve_points_won") + pl.col("second_serve_points_won")).cast(pl.Float64),
        pl.col("return_points_played").cast(pl.Float64),
        pl.col("return_points_won").cast(pl.Float64),
        pl.col("break_points_faced").cast(pl.Float64),
        pl.col("break_points_saved").cast(pl.Float64),
    )
    frame = match_stats.select("match_key", "player_id", *cols).drop_nulls()
    frame = frame.filter((pl.col("serve_points") > 0) & (pl.col("return_points_played") > 0))
    out = {}
    for row in frame.iter_rows():
        out[(row[0], row[1])] = np.array(row[2:], dtype=float)
    return out


def _new_player(params: FeatureParams) -> _Player:
    return _Player(
        _Decayed(params.stats_half_life_days, 6),
        {},
        _Decayed(params.form_half_life_days, 2),
        deque(maxlen=64),
    )


def _apply_result(
    row: tuple[Any, ...],
    players: dict[str, _Player],
    stats: dict[tuple[str, str], np.ndarray],
    tour: np.ndarray,
    params: FeatureParams,
) -> None:
    """Absorb one knowable result into both players' state (and the tour totals)."""
    mk, ra, rb, a_won, _, event, r_surface, minutes, p_elo = row
    t = _days(event)
    p_a = 0.5 if p_elo is None else float(p_elo)  # neutral: no residual if Elo is unknown
    for pid, won, p in ((ra, bool(a_won), p_a), (rb, not bool(a_won), 1.0 - p_a)):
        st = players.setdefault(pid, _new_player(params))
        s = stats.get((mk, pid))
        if s is not None:
            st.stats.add(t, s)
            tour += s
            if r_surface in SURFACES:
                st.surface.setdefault(r_surface, _Decayed(params.stats_half_life_days, 6))
                st.surface[r_surface].add(t, s)
        st.form.add(t, np.array([float(won) - p, 1.0]))
        st.recent.append((t, float(minutes) if minutes is not None else math.nan))


def _read_player(
    st: _Player, now: float, surface: str | None, tour: np.ndarray, params: FeatureParams
) -> dict[str, float]:
    """Shrunk serve/return, form and workload for one player at time ``now`` (days)."""
    mu_serve = tour[1] / tour[0] if tour[0] > 0 else 0.62
    mu_return = tour[3] / tour[2] if tour[2] > 0 else 0.38
    mu_bp = tour[5] / tour[4] if tour[4] > 0 else 0.6
    d = st.stats.at(now)
    m = params.stats_prior_points
    serve = (d[1] + m * mu_serve) / (d[0] + m)
    ret = (d[3] + m * mu_return) / (d[2] + m)
    ds = st.surface[surface].at(now) if surface in st.surface else np.zeros(6)
    ms = params.surface_stats_prior_points
    f = st.form.at(now)
    out = {
        "serve": serve,
        "return": ret,
        "surface_serve": (ds[1] + ms * serve) / (ds[0] + ms),
        "surface_return": (ds[3] + ms * ret) / (ds[2] + ms),
        "bp_saved": (d[5] + 0.1 * m * mu_bp) / (d[4] + 0.1 * m),
        "stats_points": d[0],
        "form": f[0] / (f[1] + 1.0),
    }
    past = [(e, mins) for e, mins in st.recent if e < now]
    for w in params.workload_windows_days:
        window = [mins for e, mins in past if now - e <= w]
        out[f"matches_{w}"] = float(len(window))
        out[f"minutes_{w}"] = float(np.nansum(window)) if window else 0.0
    return out


def knowable_sweep(
    targets: pl.DataFrame,
    results: pl.DataFrame,
    match_stats: pl.DataFrame,
    params: FeatureParams,
) -> pl.DataFrame:
    """Serve/return, form and workload features from results with ``knowable_at <= cutoff``.

    ``targets``: match_key, player_a_id, player_b_id, prediction_cutoff_utc, surface.
    ``results``: match_key, player_a_id, player_b_id, a_won, knowable_at_utc,
    prediction_cutoff_utc (event time), surface, minutes, p_elo (pre-match Elo probability).
    """
    stats = _stats_rows(match_stats)
    players: dict[str, _Player] = {}
    tour = np.zeros(6)  # running (non-decayed) totals for shrinkage priors
    res = results.sort("knowable_at_utc", "match_key").select(
        "match_key", "player_a_id", "player_b_id", "a_won", "knowable_at_utc",
        "prediction_cutoff_utc", "surface", "minutes", "p_elo",
    ).rows()  # fmt: skip
    tgt = (
        targets.sort("prediction_cutoff_utc", "match_key")
        .select("match_key", "player_a_id", "player_b_id", "prediction_cutoff_utc", "surface")
        .rows()
    )
    i = 0
    rows = []
    for key, pa, pb, cutoff, surface in tgt:
        while i < len(res) and res[i][4] <= cutoff:
            _apply_result(res[i], players, stats, tour, params)
            i += 1
        now = _days(cutoff)
        out: dict[str, Any] = {"match_key": key}
        for side, pid in (("a", pa), ("b", pb)):
            st = players.setdefault(pid, _new_player(params))
            for name, value in _read_player(st, now, surface, tour, params).items():
                out[f"{name}_{side}"] = value
        rows.append(out)
    return pl.DataFrame(rows)


def _codes(values: pl.Series) -> pl.Series:
    levels = sorted(v for v in values.unique().to_list() if v is not None)
    mapping = {v: i for i, v in enumerate(levels)}
    return values.replace_strict(mapping, default=None, return_dtype=pl.Float64)


def build_features(
    prepared: pl.DataFrame,
    *,
    tournaments: pl.DataFrame,
    rankings: pl.DataFrame,
    match_stats: pl.DataFrame,
    match_entry: pl.DataFrame,
    params: FeatureParams,
) -> pl.DataFrame:
    """One row per match: ``match_key`` + DIFF_FEATURES + SHARED_FEATURES (NaN = unknown)."""
    elo = elo_features(prepared, EloConfig.model_validate(params.elo_reference))
    ranks = point_in_time_ranks(prepared, rankings, staleness_days=params.rank_staleness_days)
    results = (
        prepared.filter(pl.col("elo_update_baseline"))
        .select(
            "match_key", "player_a_id", "player_b_id", "a_won", "knowable_at_utc",
            "prediction_cutoff_utc", "surface", "minutes",
        )
        .join(elo.select("match_key", "p_elo"), on="match_key", how="left")
    )  # fmt: skip
    sweep = knowable_sweep(
        prepared.select(
            "match_key", "player_a_id", "player_b_id", "prediction_cutoff_utc", "surface"
        ),
        results,
        match_stats,
        params,
    )
    age = match_entry.select("match_key", "player_id", "age_at_event_start")
    base = prepared.select(
        "match_key",
        "player_a_id",
        "player_b_id",
        "tournament_id",
        "draw_category",
        "round_ordinal",
        "best_of",
        "surface",
    ).join(tournaments.select("tournament_id", "level"), on="tournament_id", how="left")
    first_round = base.group_by("tournament_id", "draw_category").agg(
        pl.col("round_ordinal").min().alias("_first")
    )
    frame = (
        base.join(first_round, on=["tournament_id", "draw_category"], how="left")
        .join(elo, on="match_key", how="left")
        .join(ranks, on="match_key", how="left")
        .join(sweep, on="match_key", how="left")
        .join(
            age.rename({"player_id": "player_a_id", "age_at_event_start": "age_a"}),
            on=["match_key", "player_a_id"],
            how="left",
        )
        .join(
            age.rename({"player_id": "player_b_id", "age_at_event_start": "age_b"}),
            on=["match_key", "player_b_id"],
            how="left",
        )
    )
    cap = float(params.rank_cap)

    def log_rank(side: str) -> pl.Expr:
        return pl.col(f"rank_{side}").cast(pl.Float64).fill_null(cap).clip(upper_bound=cap).log()

    def days_since(side: str) -> pl.Expr:
        return pl.col(f"days_since_last_{side}").fill_null(365.0).clip(upper_bound=365.0)

    def diff(name: str) -> pl.Expr:
        return pl.col(f"{name}_a") - pl.col(f"{name}_b")

    w_short, w_long = params.workload_windows_days
    out = frame.select(
        "match_key",
        diff("elo").alias("elo_diff"),
        diff("surface_elo").alias("surface_elo_diff"),
        (pl.col("elo_n_a").log1p() - pl.col("elo_n_b").log1p()).alias("log_elo_n_diff"),
        (log_rank("a") - log_rank("b")).alias("log_rank_diff"),
        (
            pl.col("rank_a").is_null().cast(pl.Float64)
            - pl.col("rank_b").is_null().cast(pl.Float64)
        ).alias("unranked_diff"),
        diff("serve").alias("serve_diff"),
        diff("return").alias("return_diff"),
        diff("surface_serve").alias("surface_serve_diff"),
        diff("surface_return").alias("surface_return_diff"),
        diff("bp_saved").alias("bp_saved_diff"),
        (pl.col("stats_points_a").log1p() - pl.col("stats_points_b").log1p()).alias(
            "log_stats_points_diff"
        ),
        diff("form").alias("form_diff"),
        diff(f"matches_{w_short}").alias("matches_14_diff"),
        diff(f"matches_{w_long}").alias("matches_30_diff"),
        diff(f"minutes_{w_short}").alias("minutes_14_diff"),
        diff(f"minutes_{w_long}").alias("minutes_30_diff"),
        (days_since("a") - days_since("b")).alias("days_since_last_diff"),
        (
            (days_since("a") > params.inactivity_days).cast(pl.Float64)
            - (days_since("b") > params.inactivity_days).cast(pl.Float64)
        ).alias("inactive_diff"),
        diff("age").cast(pl.Float64).alias("age_diff"),
        ((pl.col("elo_a") + pl.col("elo_b")) / 2.0).alias("elo_mean"),
        pl.min_horizontal(pl.col("elo_n_a"), pl.col("elo_n_b")).log1p().alias("log_elo_n_min"),
        pl.col("best_of").cast(pl.Float64),
        pl.col("level"),
        pl.col("surface"),
        pl.col("draw_category"),
        pl.col("round_ordinal").cast(pl.Float64),
        (pl.col("round_ordinal") - pl.col("_first")).cast(pl.Float64).alias("rounds_into_draw"),
    )
    out = out.with_columns(
        _codes(out["level"]).alias("level_code"),
        _codes(out["surface"]).alias("surface_code"),
        _codes(out["draw_category"]).alias("draw_code"),
    ).drop("level", "surface", "draw_category")
    return out.select("match_key", *DIFF_FEATURES, *SHARED_FEATURES).with_columns(
        pl.col(c).cast(pl.Float64) for c in (*DIFF_FEATURES, *SHARED_FEATURES)
    )


def swap(x: np.ndarray) -> np.ndarray:
    """The same match with players A and B exchanged: negate A - B features, keep shared ones."""
    out = x.copy()
    out[:, : len(DIFF_FEATURES)] *= -1.0
    return out
