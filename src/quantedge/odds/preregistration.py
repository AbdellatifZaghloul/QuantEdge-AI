"""Machine-readable, fingerprinted pre-registration for market comparisons.

The YAML in ``configs/preregistration/`` is the frozen protocol; its SHA-256 is recorded with every
run, so any deviation is visible. Helpers here enforce the parts that must hold mechanically:
population window (no late-check or holdout years) and outcome-blind reference selection.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Literal

import polars as pl
import yaml
from pydantic import BaseModel, ConfigDict

from quantedge.odds.devig import Method

OUTCOME_COLUMNS = frozenset({"y", "a_won", "winner_id", "outcome_type", "score_raw"})


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Population(_Frozen):
    provider: str
    sport_keys: tuple[str, ...]
    canonical_levels: tuple[str, ...]
    main_draw_only: bool
    tournament_start_from: date
    tournament_start_to: date
    require_gate0_eval_eligible: bool
    require_link_status: str


class ModelPredictions(_Frozen):
    phase2_run_id: str
    elo_column: str
    rank_column: str


class Timing(_Frozen):
    decision_lead_minutes: float
    anchor: Literal["earliest_observed_commence_time"]
    primary_max_staleness_minutes: float
    sensitivity_max_staleness_minutes: tuple[float, ...]
    missing_source_time: Literal["reject", "use_observed"]


class Market(_Frozen):
    region: str
    market: str
    primary_reference_bookmaker: str
    primary_adequacy_min_availability: float
    fallback_rule: Literal["highest_availability_outcome_blind"]
    consensus_min_bookmakers: int
    devig_primary: Method
    devig_secondary: tuple[Method, ...]


class Metrics(_Frozen):
    primary: tuple[str, ...]
    contrasts: tuple[tuple[str, str], ...]
    bootstrap_block: Literal["iso_week_of_tournament_start"]
    bootstrap_replicates: int
    bootstrap_seed: int
    confidence_level: float
    report: tuple[str, ...]


class Conduct(_Frozen):
    max_confirmatory_runs: int
    information_asymmetry_note: str


class Protocol(_Frozen):
    protocol_id: str
    frozen_on: date
    purpose: str
    population: Population
    model_predictions: ModelPredictions
    timing: Timing
    market: Market
    metrics: Metrics
    conduct: Conduct


class ProtocolViolationError(ValueError):
    """Raised when an analysis step would deviate from the pre-registered protocol."""


def load_protocol(path: Path) -> tuple[Protocol, str]:
    """Return the protocol and the SHA-256 of the exact file bytes (line endings normalised)."""
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    protocol = Protocol.model_validate(yaml.safe_load(raw.decode("utf-8")))
    return protocol, hashlib.sha256(raw).hexdigest()


def assert_within_window(frame: pl.DataFrame, protocol: Protocol, *, holdout_start: date) -> None:
    """Refuse any row outside the pre-registered window (late-check and holdout years included)."""
    window = protocol.population
    if window.tournament_start_to >= holdout_start:
        raise ProtocolViolationError("protocol window reaches the sealed holdout")
    outside = frame.filter(
        ~pl.col("tournament_start_date").is_between(
            window.tournament_start_from, window.tournament_start_to
        )
    )
    if outside.height:
        raise ProtocolViolationError("rows outside the pre-registered tournament window")


def choose_reference(availability: pl.DataFrame, protocol: Protocol) -> tuple[str, str]:
    """Outcome-blind choice of the primary market reference.

    ``availability`` has one row per bookmaker; ``availability`` = share of the linked population
    with an available quote at the primary timing. It must not contain outcome columns.
    Returns ``(bookmaker_key, rule_applied)``.
    """
    leaked = OUTCOME_COLUMNS & set(availability.columns)
    if leaked:
        raise ProtocolViolationError(
            f"reference choice must be outcome-blind; got {sorted(leaked)}"
        )
    rates = dict(availability.select("bookmaker_key", "availability").iter_rows())
    preferred = protocol.market.primary_reference_bookmaker
    if rates.get(preferred, 0.0) >= protocol.market.primary_adequacy_min_availability:
        return preferred, "preferred_bookmaker_adequate"
    if not rates:
        raise ProtocolViolationError("no bookmaker has any available quote")
    best = sorted(rates.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return best, "fallback_highest_availability"
