"""DRAFT decision-time rules for a successor to ``market-benchmark-v1`` (not frozen, not active).

Pure, offline functions over *schedule listings*: one row per (snapshot, provider event) with the
start time the provider showed **in that snapshot** (no prices, no outcomes). Design rationale:
docs/reviews/market-benchmark-v2-design-review.md; draft protocol:
docs/preregistration/market-benchmark-v2-DRAFT.md.

- ``EditionCalendar`` / ``load_calendar``: the target days of each edition, fixed in a reference
  table *before* any purchase and never derived from returned data;
- ``day_anchor_population``: candidate A2 (recommended): decision = 10:00 tournament-local time on
  each calendar day; the population is what the snapshot at that instant lists for that local day;
- ``opening_anchor_population`` (A1) and ``match_pit_decisions`` (B): comparison candidates;
- ``identity_links`` / ``resolve_identity``: production ``event_links`` adapter and the
  point-in-time identity rule (concurrent claimants are excluded, never merged or chosen);
- ``apply_validity_censoring``: the only use of later schedule revisions (exclusion, never rescue);
- ``day_anchor_manifest`` / ``write_lock`` / ``v2_audit``: acquisition manifest, edition lock and
  the outcome-blind coverage audit with V2 denominators.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

from quantedge.odds.baseline import attach_participant_order, market_probabilities
from quantedge.odds.selector import SelectorConfig, select_quotes
from quantedge.odds.stage import (
    ABSOLUTE_STAGE_CEILING,
    LOCK_FILE,
    SENTINEL_PREFIX,
    TRANCHE_FILE,
    LedgerEntry,
    PlannedEvent,
    PlannedRequest,
    StageManifest,
    StagePlanError,
    StageRunStoppedError,
    grid_floor,
    is_sentinel,
    iso,
    read_ledger,
    run_stage,
)
from quantedge.odds.store import CaptureStore
from quantedge.odds.theoddsapi import TheOddsApiClient, cost_historical_odds, parse_utc

TOURNAMENT_TZ = {
    "tennis_atp_aus_open_singles": "Australia/Melbourne",
    "tennis_atp_french_open": "Europe/Paris",
    "tennis_atp_wimbledon": "Europe/London",
    "tennis_atp_us_open": "America/New_York",
}
DECISION_LOCAL_TIME = time(10, 0)
MIN_LEAD = timedelta(minutes=60)
SNAPSHOT_TOLERANCE = timedelta(minutes=10)  # admissible: T_d - 10 min < snapshot <= T_d
CLAIMABLE = ("linked", "duplicate_provider_event")
# First/last-day evidence grades. Rest-day and expected-empty columns are reporting labels only
# (every calendar day is requested), covered by a documentary waiver until officially sourced.
DATE_VERIFICATION_LEVELS = ("organizer", "organizer_derived", "none")
UTC_DT = pl.Datetime("us", "UTC")
LISTING_SCHEMA = pl.Schema(
    {
        "snapshot_utc": UTC_DT,
        "competition_key": pl.String(),
        "provider_event_id": pl.String(),
        "commence_utc": UTC_DT,
    }
)
_POP_SCHEMA = pl.Schema(
    {
        "local_day": pl.Date(),
        "day_kind": pl.String(),
        "decision_utc": UTC_DT,
        "as_of_snapshot_utc": UTC_DT,
        "provider_event_id": pl.String(),
        "as_of_start_utc": UTC_DT,
        "status": pl.String(),
    }
)


class V2IntegrationError(ValueError):
    """Inputs do not match the production schemas or the frozen calendar."""


# ------------------------------------------------------------------------------------ calendar


@dataclass(frozen=True)
class EditionCalendar:
    edition_id: str
    sport_key: str
    season: int
    canonical_tournament_id: str
    time_zone: str
    first_day: date
    last_day: date
    rest_days: tuple[date, ...]
    expected_empty_days: tuple[date, ...]
    source: str
    verified: bool  # complete-calendar verification: every field confirmed from official sources
    evidence: tuple[tuple[str, str], ...] = ()  # (field, evidence and source) as recorded
    dates_verified: str = "none"  # first/last day: organizer | organizer_derived | none

    def planned_days(self) -> tuple[date, ...]:
        """Every calendar day is requested, rest days included: the acquisition calendar never
        shrinks or grows after data are seen; empty days are reported, not skipped."""
        n = (self.last_day - self.first_day).days + 1
        return tuple(self.first_day + timedelta(days=i) for i in range(n))

    def day_kind(self, day: date) -> str:
        if day in self.rest_days:
            return "rest_day"
        if day in self.expected_empty_days:
            return "expected_no_atp_singles"
        return "play_day"

    def decision_utc(self, day: date) -> datetime:
        return local_instant(day, DECISION_LOCAL_TIME, self.time_zone)


def _dates(text: str) -> tuple[date, ...]:
    return tuple(sorted(date.fromisoformat(x) for x in text.split("|") if x))


@cache
def load_calendar() -> dict[str, EditionCalendar]:
    """The draft edition calendar (``reference/v2_draft_edition_calendar.csv``), validated."""
    text = (
        resources.files("quantedge.reference")
        .joinpath("v2_draft_edition_calendar.csv")
        .read_text(encoding="utf-8")
    )
    out: dict[str, EditionCalendar] = {}
    for r in csv.DictReader(text.splitlines()):
        cal = EditionCalendar(
            edition_id=r["edition_id"],
            sport_key=r["sport_key"],
            season=int(r["season"]),
            canonical_tournament_id=r["canonical_tournament_id"],
            time_zone=r["time_zone"],
            first_day=date.fromisoformat(r["first_day"]),
            last_day=date.fromisoformat(r["last_day"]),
            rest_days=_dates(r["rest_days"]),
            expected_empty_days=_dates(r["expected_empty_days"]),
            source=r["calendar_source"],
            verified=r["verified"].strip().lower() == "true",
            dates_verified=r.get("dates_verified") or "none",
            evidence=tuple(
                (k, f"{r[k]} {r.get(k.replace('_evidence', '_source'), '')}".strip())
                for k in (
                    "dates_evidence",
                    "rest_days_evidence",
                    "empty_days_evidence",
                    "start_of_play_evidence",
                )
                if r.get(k)
            ),
        )
        if cal.edition_id in out:
            raise V2IntegrationError(f"duplicate edition {cal.edition_id}")
        if cal.dates_verified not in DATE_VERIFICATION_LEVELS:
            raise V2IntegrationError(f"{cal.edition_id}: unknown dates_verified level")
        if cal.verified and cal.dates_verified != "organizer":
            raise V2IntegrationError(f"{cal.edition_id}: fully verified needs organizer dates")
        if TOURNAMENT_TZ.get(cal.sport_key) != cal.time_zone:
            raise V2IntegrationError(f"{cal.edition_id}: time zone does not match the sport")
        if not cal.first_day <= cal.last_day or (cal.last_day - cal.first_day).days > 15:
            raise V2IntegrationError(f"{cal.edition_id}: implausible day range")
        span = set(cal.planned_days())
        if not set(cal.rest_days) | set(cal.expected_empty_days) <= span:
            raise V2IntegrationError(f"{cal.edition_id}: special days outside the edition")
        out[cal.edition_id] = cal
    return out


# ------------------------------------------------------------------------------------ listings


def schedule_listings(store: CaptureStore) -> pl.DataFrame:
    """Schedule fields of every stored historical odds snapshot (SHA-256 re-verified on read)."""
    rows = []
    for record, capture in store.iter_captures():
        if "/historical/" not in record.endpoint:
            continue
        body = json.loads(capture.body.decode("utf-8"))
        snapshot = parse_utc(body.get("timestamp"))
        for event in body.get("data", []):
            rows.append(
                {
                    "snapshot_utc": snapshot,
                    "competition_key": event["sport_key"],
                    "provider_event_id": str(event["id"]),
                    "commence_utc": parse_utc(event["commence_time"]),
                }
            )
    frame = pl.DataFrame(rows, schema=LISTING_SCHEMA).unique()
    return frame.sort("snapshot_utc", "provider_event_id")


def local_day(ts: datetime, tz: str) -> date:
    return ts.astimezone(ZoneInfo(tz)).date()


def local_instant(day: date, clock: time, tz: str) -> datetime:
    return datetime.combine(day, clock, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def as_of_snapshot(snapshots: Sequence[datetime], decision: datetime) -> datetime | None:
    """Latest snapshot in ``(decision - 10 min, decision]`` (strict lower bound, inclusive upper);
    ``None`` means the day was *not observed*, never that no market existed."""
    eligible = [s for s in snapshots if decision - SNAPSHOT_TOLERANCE < s <= decision]
    return max(eligible) if eligible else None


# --------------------------------------------------------------------------------- populations


def _population_for_day(
    sport: pl.DataFrame,
    snapshots: Sequence[datetime],
    *,
    day: date,
    kind: str,
    decision: datetime,
    tz: str,
    min_lead: timedelta,
) -> list[dict[str, object]]:
    as_of = as_of_snapshot(snapshots, decision)
    base = {
        "local_day": day,
        "day_kind": kind,
        "decision_utc": decision,
        "as_of_snapshot_utc": as_of,
    }
    empty = {"provider_event_id": None, "as_of_start_utc": None}
    if as_of is None:
        return [base | empty | {"status": "snapshot_missing"}]
    out = []
    for row in sport.filter(pl.col("snapshot_utc") == as_of).iter_rows(named=True):
        start = row["commence_utc"]
        if local_day(start, tz) != day:
            continue  # listed for another local day: that day's decision, if any
        status = "in_population" if start - decision >= min_lead else "short_lead"
        out.append(
            base
            | {"provider_event_id": row["provider_event_id"], "as_of_start_utc": start}
            | {"status": status}
        )
    return out or [base | empty | {"status": "no_listing"}]


def _first_qualification(frame: pl.DataFrame) -> pl.DataFrame:
    """An event enters at its first qualifying decision only (a postponed match keeps it)."""
    first = (
        frame.filter(pl.col("status") == "in_population")
        .group_by("provider_event_id")
        .agg(pl.col("decision_utc").min().alias("_first"))
    )
    later = (pl.col("status") == "in_population") & (pl.col("decision_utc") > pl.col("_first"))
    return (
        frame.join(first, on="provider_event_id", how="left")
        .with_columns(
            pl.when(later)
            .then(pl.lit("already_assigned"))
            .otherwise(pl.col("status"))
            .alias("status")
        )
        .drop("_first")
    )


def _calendar_days(calendar: EditionCalendar, days: Sequence[date] | None) -> tuple[date, ...]:
    planned = calendar.planned_days()
    if days is None:
        return planned
    chosen = tuple(sorted(set(days)))
    if not set(chosen) <= set(planned):
        raise V2IntegrationError(f"{calendar.edition_id}: days outside the frozen calendar")
    return chosen


def day_anchor_population(
    listings: pl.DataFrame,
    *,
    calendar: EditionCalendar,
    days: Sequence[date] | None = None,
) -> pl.DataFrame:
    """Candidate A2 for the calendar's days (all planned days unless a subset is given): one row per
    day without listings (``snapshot_missing`` / ``no_listing``) or per listed event of that day."""
    sport = listings.filter(pl.col("competition_key") == calendar.sport_key)
    snapshots = sorted(set(sport["snapshot_utc"].to_list()))
    rows: list[dict[str, object]] = []
    for day in _calendar_days(calendar, days):
        rows += _population_for_day(
            sport,
            snapshots,
            day=day,
            kind=calendar.day_kind(day),
            decision=calendar.decision_utc(day),
            tz=calendar.time_zone,
            min_lead=MIN_LEAD,
        )
    return _first_qualification(pl.DataFrame(rows, schema=_POP_SCHEMA))


def opening_anchor_population(
    listings: pl.DataFrame,
    *,
    calendar: EditionCalendar,
    days: Sequence[date] | None = None,
    discovery_local_time: time = time(0, 0),
    lead: timedelta = MIN_LEAD,
) -> pl.DataFrame:
    """Candidate A1: ``T_d = O_d - lead``, where ``O_d`` is the earliest start listed for local day
    ``d`` in the latest snapshot at or before ``local(d, discovery_local_time)``."""
    tz = calendar.time_zone
    sport = listings.filter(pl.col("competition_key") == calendar.sport_key)
    snapshots = sorted(set(sport["snapshot_utc"].to_list()))
    rows: list[dict[str, object]] = []
    for day in _calendar_days(calendar, days):
        kind = calendar.day_kind(day)
        cutoff = local_instant(day, discovery_local_time, tz)
        before = [s for s in snapshots if s <= cutoff]
        starts = (
            [
                c
                for c in sport.filter(pl.col("snapshot_utc") == max(before))["commence_utc"]
                if local_day(c, tz) == day and c > cutoff
            ]
            if before
            else []
        )
        if not starts:
            rows.append(
                {"local_day": day, "day_kind": kind, "decision_utc": None,
                 "as_of_snapshot_utc": None, "provider_event_id": None, "as_of_start_utc": None,
                 "status": "no_discovery" if not before else "no_listing"}
            )  # fmt: skip
            continue
        rows += _population_for_day(
            sport,
            snapshots,
            day=day,
            kind=kind,
            decision=min(starts) - lead,
            tz=tz,
            min_lead=timedelta(0),
        )
    return _first_qualification(pl.DataFrame(rows, schema=_POP_SCHEMA))


def match_pit_decisions(
    listings: pl.DataFrame, *, sport_key: str, lead: timedelta = MIN_LEAD
) -> pl.DataFrame:
    """Candidate B on the snapshots present: decision = first snapshot ``s`` at which the start
    listed *in s* satisfies ``0 < start - s <= lead`` (``as_of_snapshot_utc`` = ``s``)."""
    rows = []
    sport = listings.filter(pl.col("competition_key") == sport_key).sort("snapshot_utc")
    for (pid,), group in sport.group_by(["provider_event_id"], maintain_order=True):
        hit = next(
            ((s, c) for s, c in group.select("snapshot_utc", "commence_utc").iter_rows()
             if timedelta(0) < c - s <= lead),
            None,
        )  # fmt: skip
        rows.append(
            {
                "local_day": None,
                "day_kind": None,
                "decision_utc": hit[0] if hit else None,
                "as_of_snapshot_utc": hit[0] if hit else None,
                "provider_event_id": pid,
                "as_of_start_utc": hit[1] if hit else None,
                "status": "in_population" if hit else "not_triggered",
            }
        )
    return pl.DataFrame(rows, schema=_POP_SCHEMA)


# --------------------------------------------------------------------------- validity censoring


def post_start_evidence(listings: pl.DataFrame) -> pl.DataFrame:
    """Earliest evidence from *any* snapshot that an event had started (a listed start at or before
    the snapshot showing it). Used only to exclude decisions at or after that instant."""
    started = listings.filter(pl.col("commence_utc") <= pl.col("snapshot_utc"))
    return started.group_by("provider_event_id").agg(
        pl.col("commence_utc").min().alias("started_by_utc")
    )


def apply_validity_censoring(population: pl.DataFrame, listings: pl.DataFrame) -> pl.DataFrame:
    started = (
        (pl.col("status") == "in_population")
        & pl.col("started_by_utc").is_not_null()
        & (pl.col("started_by_utc") <= pl.col("decision_utc"))
    )
    return (
        population.join(post_start_evidence(listings), on="provider_event_id", how="left")
        .with_columns(
            pl.when(started)
            .then(pl.lit("started_before_decision"))
            .otherwise(pl.col("status"))
            .alias("status")
        )
        .drop("started_by_utc")
    )


# ------------------------------------------------------------------------------------ identity

_LINK_COLUMNS = ("event_id", "status", "match_key", "participant_1_side")
_EVENT_COLUMNS = ("event_id", "provider_event_id", "competition_key")


def identity_links(event_links: pl.DataFrame, odds_events: pl.DataFrame) -> pl.DataFrame:
    """Adapter from the production ``event_links`` / ``odds_events`` tables (keyed by
    ``event_id``; link column ``status``) to the V2 identity frame (keyed by
    ``provider_event_id``; column ``link_status``). Fails loudly instead of guessing."""
    for name, frame, cols in (
        ("event_links", event_links, _LINK_COLUMNS),
        ("odds_events", odds_events, _EVENT_COLUMNS),
    ):
        missing = [c for c in cols if c not in frame.columns]
        if missing:
            raise V2IntegrationError(f"{name} lacks columns {missing}")
        if frame["event_id"].n_unique() != frame.height:
            raise V2IntegrationError(f"{name}: event_id is not unique")
    if odds_events["provider_event_id"].n_unique() != odds_events.height:
        raise V2IntegrationError("odds_events: provider_event_id is not unique")
    unknown = event_links.join(odds_events, on="event_id", how="anti")
    if unknown.height:
        raise V2IntegrationError(f"{unknown.height} event_links rows have no odds event")
    joined = event_links.select(_LINK_COLUMNS).join(
        odds_events.select(_EVENT_COLUMNS), on="event_id", how="inner"
    )
    claimable_without_key = joined.filter(
        pl.col("status").is_in(CLAIMABLE) & pl.col("match_key").is_null()
    )
    if claimable_without_key.height:
        raise V2IntegrationError("a linked or duplicate row has no match_key")
    return joined.rename({"status": "link_status"}).select(
        "provider_event_id", "competition_key", "event_id", "match_key", "link_status",
        "participant_1_side",
    )  # fmt: skip


def resolve_identity(
    population: pl.DataFrame, identity: pl.DataFrame, listings: pl.DataFrame
) -> pl.DataFrame:
    """Point-in-time identity for in-population rows.

    Claimants of a canonical match at a decision are *all* provider events listed in the decision's
    as-of snapshot (for any day) whose link status is ``linked`` or ``duplicate_provider_event``
    with that ``match_key``. A match is resolved at its first in-population decision: one claimant
    there -> ``unique``; two or more -> every row of that match is ``identity_conflict`` and it is
    excluded for good. Later rows -> ``already_assigned``. Not linkable -> ``not_linked``. Nothing
    is merged and no claimant is chosen.
    """
    claim = identity.filter(pl.col("link_status").is_in(CLAIMABLE)).select(
        "provider_event_id", "match_key"
    )
    concurrent = (
        listings.select(pl.col("snapshot_utc").alias("as_of_snapshot_utc"), "provider_event_id")
        .join(claim, on="provider_event_id")
        .group_by("as_of_snapshot_utc", "match_key")
        .agg(pl.col("provider_event_id").n_unique().alias("_claimants"))
    )
    pop = (
        population.filter(pl.col("status") == "in_population")
        .join(
            identity.select("provider_event_id", "event_id", "match_key", "link_status"),
            on="provider_event_id",
            how="left",
        )
        .with_columns(
            (pl.col("link_status").is_in(CLAIMABLE) & pl.col("match_key").is_not_null())
            .fill_null(False)
            .alias("_claimable")
        )
        .join(concurrent, on=["as_of_snapshot_utc", "match_key"], how="left")
    )
    claims = pop.filter(pl.col("_claimable"))
    first = claims.group_by("match_key").agg(pl.col("decision_utc").min().alias("_first"))
    at_first = (
        claims.join(first, on="match_key")
        .filter(pl.col("decision_utc") == pl.col("_first"))
        .group_by("match_key")
        .agg(
            pl.max_horizontal(pl.col("_claimants").max(), pl.len()).alias("_n_first"),
        )
    )
    return (
        pop.join(first, on="match_key", how="left")
        .join(at_first, on="match_key", how="left")
        .with_columns(
            pl.when(~pl.col("_claimable"))
            .then(pl.lit("not_linked"))
            .when(pl.col("_n_first") > 1)
            .then(pl.lit("identity_conflict"))
            .when(pl.col("decision_utc") > pl.col("_first"))
            .then(pl.lit("already_assigned"))
            .otherwise(pl.lit("unique"))
            .alias("identity")
        )
        .drop("_claimable", "_claimants", "_first", "_n_first")
    )


def links_for_market(resolved: pl.DataFrame, identity: pl.DataFrame) -> pl.DataFrame:
    """``event_links``-shaped frame for ``market_probabilities``: V2-``unique`` events only, status
    ``linked`` (so store-wide duplicates resolved point-in-time are not silently dropped)."""
    unique = resolved.filter(pl.col("identity") == "unique").select("provider_event_id")
    return identity.join(unique, on="provider_event_id").select(
        "event_id", pl.lit("linked").alias("status"), "match_key", "participant_1_side"
    )


# ------------------------------------------------------------------------- acquisition, locks


def day_anchor_manifest(
    *,
    name: str,
    calendar: EditionCalendar,
    days: Sequence[date],
    markets: tuple[str, ...] = ("h2h",),
    regions: tuple[str, ...] = ("eu",),
    stage_ceiling: int = ABSOLUTE_STAGE_CEILING,
) -> StageManifest:
    """One request per calendar day at ``T_d`` (fixed before any data). Each request carries one
    internal sentinel holding ``T_d``; the runner then stops unless the returned snapshot is in
    ``(T_d - 10 min, T_d]``. Sentinels are never counted as events or availability."""
    cost = cost_historical_odds(markets, regions)
    requests = []
    for i, day in enumerate(_calendar_days(calendar, days), start=1):
        decision = calendar.decision_utc(day)
        sentinel = PlannedEvent(
            provider_event_id=f"{SENTINEL_PREFIX}{calendar.edition_id}:{day.isoformat()}",
            scheduled_start_utc=iso(decision + MIN_LEAD),
            decision_utc=iso(decision),
            discovered_later=False,
        )
        requests.append(
            PlannedRequest(
                request_id=f"{name}-{i:02d}",
                sport_key=calendar.sport_key,
                request_date_utc=iso(decision),
                expected_snapshot_utc=iso(
                    grid_floor(decision, cadence_minutes=10, offset_minutes=5)
                ),
                credits=cost,
                events=(sentinel,),
            )
        )
    total = cost * len(requests)
    if total > min(stage_ceiling, ABSOLUTE_STAGE_CEILING):
        raise StagePlanError(f"{total} credits exceed the stage ceiling; split the manifest")
    return StageManifest(
        name=name,
        sport_key=calendar.sport_key,
        discovery_capture_id=f"none: fixed calendar {calendar.edition_id} (DRAFT v2 A2)",
        discovery_snapshot_utc=requests[0].request_date_utc if requests else "",
        lead_minutes=MIN_LEAD.total_seconds() / 60,
        max_gap_minutes=SNAPSHOT_TOLERANCE.total_seconds() / 60,
        cadence_minutes=10,
        grid_offset_minutes=5,
        markets=markets,
        regions=regions,
        stage_ceiling=stage_ceiling,
        planned_credits=total,
        requests=tuple(requests),
        strict_snapshot_window=True,
    )


def manifest_days(manifest: StageManifest, calendar: EditionCalendar) -> tuple[date, ...]:
    """Calendar days of a day-anchor manifest; every request must match the calendar exactly."""
    days = []
    for r in manifest.requests:
        sentinels = [e for e in r.events if is_sentinel(e.provider_event_id)]
        if len(sentinels) != 1 or len(r.events) != 1:
            raise V2IntegrationError(f"{r.request_id}: not a day-anchor request")
        day = date.fromisoformat(sentinels[0].provider_event_id.rsplit(":", 1)[1])
        if (
            r.request_date_utc != iso(calendar.decision_utc(day))
            or day not in calendar.planned_days()
        ):
            raise V2IntegrationError(f"{r.request_id}: request does not match the frozen calendar")
        days.append(day)
    return tuple(days)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_lock(stage_dir: Path, store: CaptureStore) -> dict[str, Any]:
    """Freeze a stage's acquisition: manifest and ledger hashes plus every capture it paid for.
    Write-once; afterwards ``run_stage`` refuses any further request for this stage."""
    lock_path = stage_dir / LOCK_FILE
    if lock_path.exists():
        raise V2IntegrationError(f"{lock_path} already exists")
    ledger = read_ledger(stage_dir / "ledger.jsonl")
    by_id = {r.capture_id: r for r in store.records()}
    lock = {
        "manifest_sha256": _sha(stage_dir / "manifest.json"),
        "ledger_sha256": _sha(stage_dir / "ledger.jsonl") if ledger else None,
        "captures": [
            {"capture_id": e.capture_id, "sha256": by_id[e.capture_id].sha256} for e in ledger
        ],
        "credits": ledger[-1].cumulative_credits if ledger else 0,
    }
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return lock


# ---------------------------------------------------------------------------------- v2 audit


def _distinct_sources(available: pl.DataFrame) -> pl.DataFrame:
    pair = pl.concat_str(
        [
            pl.min_horizontal("odds_1", "odds_2").round(3).cast(pl.String),
            pl.max_horizontal("odds_1", "odds_2").round(3).cast(pl.String),
        ],
        separator="/",
    )
    return (
        available.with_columns(pair.alias("_pair"))
        .group_by("event_id")
        .agg(
            pl.col("_pair").n_unique().alias("distinct_sources"),
            pl.col("bookmaker_key").n_unique().alias("books"),
        )
    )


def _quantiles(values: pl.Series) -> list[float] | None:
    values = values.drop_nulls().cast(pl.Float64)
    if not values.len():
        return None
    return [round(float(values.quantile(q) or 0.0), 2) for q in (0.0, 0.5, 0.9, 1.0)]


def v2_audit(
    *,
    calendar: EditionCalendar,
    manifests: Sequence[tuple[StageManifest, Sequence[LedgerEntry], bool]],
    listings: pl.DataFrame,
    events: pl.DataFrame,
    quotes: pl.DataFrame,
    identity: pl.DataFrame,
    canonical_matches: pl.DataFrame,
    requested_only: bool = True,
) -> dict[str, Any]:
    """Outcome-blind A2 coverage audit with V2 denominators (no V1 ``stage_audit`` reuse).

    ``manifests``: (manifest, ledger entries, locked?) for the stages of this edition. With
    ``requested_only`` (default) only days whose request is in a ledger are audited; planned days
    not yet requested are listed as ``pending_days``. A partial acquisition (e.g. one tranche) is
    audited as such, and no complete edition or lock is required.
    ``canonical_matches`` needs only identity/timing columns: ``match_key``, ``tournament_id``,
    ``draw_category``, ``match_day_lo``, ``match_day_hi``, ``prediction_cutoff_utc``.
    """
    planned: dict[date, bool] = {}
    for m, ledger, _ in manifests:
        paid = {e.request_id for e in ledger}
        for req, d in zip(m.requests, manifest_days(m, calendar), strict=True):
            planned[d] = planned.get(d, False) or req.request_id in paid
    days = sorted(d for d, paid in planned.items() if paid or not requested_only)
    pending = sorted(d for d, paid in planned.items() if not paid and requested_only)
    if not days:
        raise V2IntegrationError("no requested manifest days to audit")
    sport = listings.filter(pl.col("competition_key") == calendar.sport_key)
    pop = apply_validity_censoring(
        day_anchor_population(sport, calendar=calendar, days=days), sport
    )
    resolved = resolve_identity(pop, identity, sport)
    cutoffs = canonical_matches.select("match_key", "prediction_cutoff_utc")
    resolved = resolved.join(cutoffs, on="match_key", how="left").with_columns(
        pl.when(
            (pl.col("identity") == "unique")
            & (pl.col("decision_utc") < pl.col("prediction_cutoff_utc"))
        )
        .then(pl.lit("market_before_model_cutoff"))
        .otherwise(pl.col("identity"))
        .alias("identity")
    )
    unique = resolved.filter(pl.col("identity") == "unique")
    decisions = unique.select(
        "event_id", pl.col("decision_utc").alias("decision_time_utc")
    ).with_columns(pl.col("decision_time_utc").cast(UTC_DT))

    def availability(minutes: float) -> tuple[pl.DataFrame, pl.DataFrame]:
        sel = select_quotes(quotes, decisions, SelectorConfig(max_staleness_minutes=minutes))
        return sel, sel.filter(pl.col("status") == "available")

    sel30, av30 = availability(30.0)
    src30 = _distinct_sources(av30)
    sensitivity = {}
    for minutes in (15.0, 60.0):
        _, av = availability(minutes)
        src = _distinct_sources(av)
        sensitivity[f"{minutes:g}"] = {
            "ge1": src.height,
            "ge2_distinct": int((src["distinct_sources"] >= 2).sum()),
        }
    ordered = attach_participant_order(sel30, events)
    probs = market_probabilities(ordered, links_for_market(resolved, identity), ("proportional",))
    devig_usable = probs.filter(pl.col("usable"))["event_id"].n_unique() if probs.height else 0

    main = canonical_matches.filter(
        (pl.col("tournament_id") == calendar.canonical_tournament_id)
        & (pl.col("draw_category") == "tour_main")
    )
    lo, hi = days[0], days[-1]
    certain = main.filter((pl.col("match_day_lo") >= lo) & (pl.col("match_day_hi") <= hi))
    possible = main.filter((pl.col("match_day_lo") <= hi) & (pl.col("match_day_hi") >= lo))
    unique_keys = set(unique["match_key"].to_list())
    fresh_keys = set(
        unique.join(src30.filter(pl.col("distinct_sources") >= 2), on="event_id")["match_key"]
    )
    priced = sel30.filter(pl.col("bookmaker_key").is_not_null())
    day_rows = (
        pop.group_by("local_day", "day_kind")
        .agg(
            pl.col("status").filter(pl.col("provider_event_id").is_null()).first().alias("empty"),
            (pl.col("status") == "in_population").sum().alias("in_population"),
        )
        .sort("local_day")
    )
    main_keys = set(main["match_key"].to_list())
    unique_main_by_day = Counter(
        r["local_day"] for r in unique.iter_rows(named=True) if r["match_key"] in main_keys
    )
    credits = sum(ledger[-1].cumulative_credits if ledger else 0 for _, ledger, _ in manifests)
    return {
        "edition": calendar.edition_id,
        "calendar_verified": calendar.verified,
        "audited_days": [d.isoformat() for d in days],
        "pending_days": [d.isoformat() for d in pending],
        "days": [
            {
                "day": r["local_day"].isoformat(),
                "kind": r["day_kind"],
                "status": r["empty"] or "observed",
                "in_population": r["in_population"],
                "unique_canonical_main_draw": unique_main_by_day.get(r["local_day"], 0),
            }
            for r in day_rows.iter_rows(named=True)
        ],
        "denominators": {
            "canonical_main_draw_certainly_in_days": certain.height,
            "canonical_main_draw_possibly_in_days": possible.height,
            "listed_in_population_events": int((pop["status"] == "in_population").sum()),
            "unique_matches": len(unique_keys),
            "unique_and_certain": len(unique_keys & set(certain["match_key"])),
            "fresh30_ge1_events": src30.height,
            "fresh30_ge2_distinct_matches": len(fresh_keys),
            "fresh30_ge2_distinct_and_certain": len(fresh_keys & set(certain["match_key"])),
            "devig_usable_events": devig_usable,
        },
        "population_status": dict(Counter(pop["status"].to_list())),
        "identity_status": dict(Counter(resolved["identity"].to_list())),
        "freshness_sensitivity": sensitivity,
        "quote_age_gating_all_priced": _quantiles(priced["staleness_minutes"]),
        "quote_age_available": _quantiles(av30["staleness_minutes"]),
        "pinnacle_events": av30.filter(pl.col("bookmaker_key") == "pinnacle")[
            "event_id"
        ].n_unique(),
        "horizon_minutes": _quantiles(
            (unique["as_of_start_utc"] - unique["decision_utc"]).dt.total_minutes()
        ),
        "credits_spent": credits,
        "all_stages_locked": all(locked for _, _, locked in manifests),
    }


# ----------------------------------------------------------------------------------- tranches


@dataclass(frozen=True)
class Tranche:
    number: int
    request_ids: tuple[str, ...]
    max_credits: int  # additional credits this tranche may spend
    cumulative_cap: int  # ledger total after this tranche can never exceed this
    requires_gate_of: int | None  # earlier tranche whose offline gate must have passed


def _gate_path(stage_dir: Path, number: int) -> Path:
    return stage_dir / f"tranche-{number}-gate.json"


def _stop_path(stage_dir: Path, number: int) -> Path:
    return stage_dir / f"tranche-{number}-stopped.json"


def _ledger_prefix_sha(ledger_path: Path, n: int) -> str:
    lines = ledger_path.read_text(encoding="utf-8").splitlines()[:n] if ledger_path.exists() else []
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def write_tranche_plan(
    stage_dir: Path, *, sizes: Sequence[int], max_credits: Sequence[int]
) -> list[Tranche]:
    """Split an existing manifest, in order, into separately approved tranches (write-once).

    The manifest itself is untouched; its SHA-256 is bound into the plan. Each tranche after the
    first requires a passing offline gate of the previous one.
    """
    path = stage_dir / TRANCHE_FILE
    if path.exists():
        raise V2IntegrationError(f"{path} already exists")
    manifest = StageManifest.from_json((stage_dir / "manifest.json").read_text(encoding="utf-8"))
    if len(sizes) != len(max_credits) or sum(sizes) != len(manifest.requests) or min(sizes) < 1:
        raise V2IntegrationError("tranche sizes must partition the requests of the manifest")
    tranches, start, cumulative = [], 0, 0
    for i, (size, cap) in enumerate(zip(sizes, max_credits, strict=True), start=1):
        chosen = manifest.requests[start : start + size]
        if cap < sum(r.credits for r in chosen):
            raise V2IntegrationError(f"tranche {i}: cap {cap} below its planned cost")
        cumulative += cap
        tranches.append(
            Tranche(i, tuple(r.request_id for r in chosen), cap, cumulative, i - 1 or None)
        )
        start += size
    if cumulative > manifest.planned_credits:
        raise V2IntegrationError("tranche caps exceed the planned credits of the manifest")
    plan = {
        "manifest_sha256": _sha(stage_dir / "manifest.json"),
        "tranches": [t.__dict__ | {"request_ids": list(t.request_ids)} for t in tranches],
    }
    path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return tranches


def read_tranche_plan(stage_dir: Path) -> list[Tranche]:
    plan = json.loads((stage_dir / TRANCHE_FILE).read_text(encoding="utf-8"))
    if plan["manifest_sha256"] != _sha(stage_dir / "manifest.json"):
        raise V2IntegrationError("manifest changed after the tranche plan was written")
    return [
        Tranche(t["number"], tuple(t["request_ids"]), t["max_credits"], t["cumulative_cap"],
                t["requires_gate_of"])
        for t in plan["tranches"]
    ]  # fmt: skip


def run_tranche(
    stage_dir: Path,
    number: int,
    *,
    store: CaptureStore,
    client: TheOddsApiClient | None = None,
    execute: bool = False,
) -> list[dict[str, Any]]:
    """Run exactly one tranche of a planned stage (dry run unless ``execute``).

    Refused when any tranche has stopped (a marker is written on a runner stop; an owner decision
    is needed), or when the required earlier gate is missing, not ``pass``, or no longer matches
    the ledger it was computed on.
    """
    tranches = {t.number: t for t in read_tranche_plan(stage_dir)}
    if number not in tranches:
        raise V2IntegrationError(f"no tranche {number}")
    stopped = sorted(stage_dir.glob("tranche-*-stopped.json"))
    if stopped:
        raise StageRunStoppedError(f"{stopped[0].name} present: a tranche stopped; owner review")
    tranche = tranches[number]
    ledger_path = stage_dir / "ledger.jsonl"
    if tranche.requires_gate_of is not None:
        gate_path = _gate_path(stage_dir, tranche.requires_gate_of)
        if not gate_path.exists():
            raise StageRunStoppedError(f"tranche {number} needs {gate_path.name}")
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        if gate["status"] != "pass":
            raise StageRunStoppedError(f"tranche {tranche.requires_gate_of} gate: {gate['status']}")
        if gate["ledger_prefix_sha256"] != _ledger_prefix_sha(ledger_path, gate["ledger_entries"]):
            raise StageRunStoppedError("ledger changed since the gate was computed")
    manifest = StageManifest.from_json((stage_dir / "manifest.json").read_text(encoding="utf-8"))
    try:
        return run_stage(
            manifest,
            store=store,
            ledger_path=ledger_path,
            client=client,
            execute=execute,
            allowed_request_ids=frozenset(tranche.request_ids),
            credit_cap=tranche.cumulative_cap,
        )
    except StageRunStoppedError as exc:
        if execute:
            _stop_path(stage_dir, number).write_text(
                json.dumps({"tranche": number, "reason": str(exc)}) + "\n", encoding="utf-8"
            )
        raise


def check_tranche(
    stage_dir: Path, number: int, *, calendar: EditionCalendar, audit_report: dict[str, Any]
) -> dict[str, Any]:
    """Offline gate for one tranche (write-once).

    ``pass`` only if every request of the tranche is paid and no later one is, each returned
    snapshot lies inside ``(T_d - 10 min, T_d]``, spend is within the cumulative cap, the tranche
    never stopped, and each tranche day is observed with >= 1 unique canonical main-draw match.
    Anything missing is ``inconclusive``; any violation is ``fail``. Neither unlocks a later
    tranche.
    """
    path = _gate_path(stage_dir, number)
    if path.exists():
        raise V2IntegrationError(f"{path} already exists")
    tranches = {t.number: t for t in read_tranche_plan(stage_dir)}
    tranche = tranches[number]
    manifest = StageManifest.from_json((stage_dir / "manifest.json").read_text(encoding="utf-8"))
    request_days = manifest_days(manifest, calendar)
    day_of = dict(zip((r.request_id for r in manifest.requests), request_days, strict=True))
    ledger_path = stage_dir / "ledger.jsonl"
    ledger = read_ledger(ledger_path)
    paid = {e.request_id: e for e in ledger}
    allowed = {rid for t in tranches.values() if t.number <= number for rid in t.request_ids}
    fails: list[str] = []
    gaps: list[str] = []
    if _stop_path(stage_dir, number).exists():
        fails.append("the tranche stopped during execution")
    missing = [rid for rid in tranche.request_ids if rid not in paid]
    if missing:
        gaps.append(f"unpaid requests: {missing}")
    if set(paid) - allowed:
        fails.append("ledger contains requests beyond this tranche")
    if ledger and ledger[-1].cumulative_credits > tranche.cumulative_cap:
        fails.append("cumulative credits exceed the tranche cap")
    days = {d["day"]: d for d in audit_report.get("days", [])}
    for rid in tranche.request_ids:
        entry, day = paid.get(rid), day_of[rid]
        if entry is not None:
            returned = parse_utc(entry.returned_snapshot_utc)
            decision = calendar.decision_utc(day)
            if returned is None or not decision - SNAPSHOT_TOLERANCE < returned <= decision:
                fails.append(f"{day}: snapshot outside (T_d - 10 min, T_d]")
        row = days.get(day.isoformat())
        if row is None:
            gaps.append(f"{day}: not in the audit report")
        elif row["status"] != "observed" or row.get("unique_canonical_main_draw", 0) < 1:
            fails.append(f"{day}: no unique ATP main-draw listing ({row['status']})")
    status = "fail" if fails else "inconclusive" if gaps else "pass"
    gate = {
        "tranche": number,
        "status": status,
        "reasons": fails + gaps,
        "manifest_sha256": _sha(stage_dir / "manifest.json"),
        "ledger_entries": len(ledger),
        "ledger_prefix_sha256": _ledger_prefix_sha(ledger_path, len(ledger)),
        "audit_denominators": audit_report.get("denominators"),
    }
    path.write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return gate
