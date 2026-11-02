"""DRAFT decision-time rules for a successor to ``market-benchmark-v1`` (not frozen, not active).

Pure, offline functions over *schedule listings*: one row per (snapshot, provider event) with the
start time the provider showed **in that snapshot** (no prices, no outcomes). They implement the
candidate anchors compared in docs/reviews/market-benchmark-v2-design-review.md:

- ``day_anchor_population``: decision = fixed local clock time on each tournament day (candidate A2,
  recommended); the population is what the snapshot at that time lists for that local day;
- ``opening_anchor_population``: decision = earliest start listed for the day in a discovery
  snapshot taken at a fixed earlier time, minus the lead (candidate A1);
- ``match_pit_decisions``: decision = first poll at which a match's *then-listed* start is within
  the lead (candidate B).

Every rule uses only information observed at or before the decision. Later schedule revisions are
used for exactly one thing: ``post_start_evidence`` excludes a decision when any later listing shows
the match had already started by then (validity censoring, never rescue).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import polars as pl

from quantedge.odds.stage import (
    ABSOLUTE_STAGE_CEILING,
    PlannedEvent,
    PlannedRequest,
    StageManifest,
    StagePlanError,
    grid_floor,
    iso,
)
from quantedge.odds.store import CaptureStore
from quantedge.odds.theoddsapi import cost_historical_odds, parse_utc

TOURNAMENT_TZ = {
    "tennis_atp_aus_open_singles": "Australia/Melbourne",
    "tennis_atp_french_open": "Europe/Paris",
    "tennis_atp_wimbledon": "Europe/London",
    "tennis_atp_us_open": "America/New_York",
}
UTC_DT = pl.Datetime("us", "UTC")
LISTING_SCHEMA = pl.Schema(
    {
        "snapshot_utc": UTC_DT,
        "competition_key": pl.String(),
        "provider_event_id": pl.String(),
        "commence_utc": UTC_DT,
    }
)


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
    return (
        pl.DataFrame(rows, schema=LISTING_SCHEMA).unique().sort("snapshot_utc", "provider_event_id")
    )


def local_day(ts: datetime, tz: str) -> date:
    return ts.astimezone(ZoneInfo(tz)).date()


def local_instant(day: date, clock: time, tz: str) -> datetime:
    return datetime.combine(day, clock, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def _as_of_snapshot(
    snapshots: list[datetime], decision: datetime, tolerance: timedelta
) -> datetime | None:
    """Latest stored snapshot in ``(decision - tolerance, decision]``, i.e. what a request at the
    decision would return on a regular grid. ``None`` means *not observed*, not *no market*."""
    eligible = [s for s in snapshots if decision - tolerance < s <= decision]
    return max(eligible) if eligible else None


def _population_for_day(
    listings: pl.DataFrame,
    *,
    sport_key: str,
    day: date,
    decision: datetime,
    tz: str,
    min_lead: timedelta,
    tolerance: timedelta,
) -> list[dict[str, object]]:
    sport = listings.filter(pl.col("competition_key") == sport_key)
    snapshots = sorted(set(sport["snapshot_utc"].to_list()))
    as_of = _as_of_snapshot(snapshots, decision, tolerance)
    base = {"local_day": day, "decision_utc": decision, "as_of_snapshot_utc": as_of}
    if as_of is None:
        return [
            base | {"provider_event_id": None, "as_of_start_utc": None, "status": "not_observed"}
        ]
    out = []
    for row in sport.filter(pl.col("snapshot_utc") == as_of).iter_rows(named=True):
        start = row["commence_utc"]
        if local_day(start, tz) != day:
            continue  # listed for another local day: belongs to that day's decision, if any
        status = "in_population" if start - decision >= min_lead else "short_lead"
        out.append(
            base
            | {
                "provider_event_id": row["provider_event_id"],
                "as_of_start_utc": start,
                "status": status,
            }
        )
    return out or [
        base | {"provider_event_id": None, "as_of_start_utc": None, "status": "no_listing"}
    ]


def _first_qualification(frame: pl.DataFrame) -> pl.DataFrame:
    """An event enters the population at its first qualifying decision only (knowable then)."""
    if frame.is_empty():
        return frame
    first = (
        frame.filter(pl.col("status") == "in_population")
        .group_by("provider_event_id")
        .agg(pl.col("decision_utc").min().alias("_first"))
    )
    return (
        frame.join(first, on="provider_event_id", how="left")
        .with_columns(
            pl.when(
                (pl.col("status") == "in_population") & (pl.col("decision_utc") > pl.col("_first"))
            )
            .then(pl.lit("already_assigned"))
            .otherwise(pl.col("status"))
            .alias("status")
        )
        .drop("_first")
    )


_POP_SCHEMA = pl.Schema(
    {
        "local_day": pl.Date(),
        "decision_utc": UTC_DT,
        "as_of_snapshot_utc": UTC_DT,
        "provider_event_id": pl.String(),
        "as_of_start_utc": UTC_DT,
        "status": pl.String(),
    }
)


def _days(listings: pl.DataFrame, sport_key: str, tz: str) -> list[date]:
    sport = listings.filter(pl.col("competition_key") == sport_key)
    if sport.is_empty():
        return []
    stamps = sport["snapshot_utc"].to_list() + sport["commence_utc"].to_list()
    lo, hi = min(local_day(t, tz) for t in stamps), max(local_day(t, tz) for t in stamps)
    return [lo + timedelta(days=i) for i in range((hi - lo).days + 1)]


def day_anchor_population(
    listings: pl.DataFrame,
    *,
    sport_key: str,
    local_decision_time: time = time(10, 0),
    min_lead_minutes: float = 60.0,
    snapshot_tolerance_minutes: float = 10.0,
) -> pl.DataFrame:
    """Candidate A2: ``T_d = local(d, local_decision_time)``; population = events listed in the
    snapshot at ``T_d`` whose then-listed start falls on local day ``d`` and is >= lead after
    ``T_d``.
    """
    tz = TOURNAMENT_TZ[sport_key]
    rows: list[dict[str, object]] = []
    for day in _days(listings, sport_key, tz):
        rows += _population_for_day(
            listings,
            sport_key=sport_key,
            day=day,
            decision=local_instant(day, local_decision_time, tz),
            tz=tz,
            min_lead=timedelta(minutes=min_lead_minutes),
            tolerance=timedelta(minutes=snapshot_tolerance_minutes),
        )
    return _first_qualification(pl.DataFrame(rows, schema=_POP_SCHEMA))


def opening_anchor_population(
    listings: pl.DataFrame,
    *,
    sport_key: str,
    discovery_local_time: time = time(0, 0),
    lead_minutes: float = 60.0,
    snapshot_tolerance_minutes: float = 10.0,
) -> pl.DataFrame:
    """Candidate A1: ``T_d = O_d - lead`` where ``O_d`` is the earliest start listed for local day
    ``d`` in the latest snapshot at or before ``K_d = local(d, discovery_local_time)``."""
    tz = TOURNAMENT_TZ[sport_key]
    sport = listings.filter(pl.col("competition_key") == sport_key)
    rows: list[dict[str, object]] = []
    for day in _days(listings, sport_key, tz):
        cutoff = local_instant(day, discovery_local_time, tz)
        before = sport.filter(pl.col("snapshot_utc") <= cutoff)
        if before.is_empty():
            rows.append(
                {"local_day": day, "decision_utc": None, "as_of_snapshot_utc": None,
                 "provider_event_id": None, "as_of_start_utc": None, "status": "no_discovery"}
            )  # fmt: skip
            continue
        discovery = before.filter(pl.col("snapshot_utc") == before["snapshot_utc"].max())
        starts = [
            s for s in discovery["commence_utc"].to_list() if local_day(s, tz) == day and s > cutoff
        ]
        if not starts:
            rows.append(
                {"local_day": day, "decision_utc": None, "as_of_snapshot_utc": None,
                 "provider_event_id": None, "as_of_start_utc": None, "status": "no_listing"}
            )  # fmt: skip
            continue
        rows += _population_for_day(
            listings,
            sport_key=sport_key,
            day=day,
            decision=min(starts) - timedelta(minutes=lead_minutes),
            tz=tz,
            min_lead=timedelta(minutes=0),
            tolerance=timedelta(minutes=snapshot_tolerance_minutes),
        )
    return _first_qualification(pl.DataFrame(rows, schema=_POP_SCHEMA))


def match_pit_decisions(
    listings: pl.DataFrame, *, sport_key: str, lead_minutes: float = 60.0
) -> pl.DataFrame:
    """Candidate B on the given poll grid (the snapshots present in ``listings``): decision = first
    snapshot ``s`` at which the start listed *in s* satisfies ``0 < start - s <= lead``."""
    lead = timedelta(minutes=lead_minutes)
    rows = []
    sport = listings.filter(pl.col("competition_key") == sport_key).sort("snapshot_utc")
    for (pid,), group in sport.group_by(["provider_event_id"], maintain_order=True):
        decision = start = None
        for s, c in group.select("snapshot_utc", "commence_utc").iter_rows():
            if timedelta(0) < c - s <= lead:
                decision, start = s, c
                break
        rows.append(
            {
                "provider_event_id": pid,
                "decision_utc": decision,
                "as_of_start_utc": start,
                "status": "in_population" if decision is not None else "not_triggered",
            }
        )
    schema = {
        k: _POP_SCHEMA[k]
        for k in ("provider_event_id", "decision_utc", "as_of_start_utc", "status")
    }
    return pl.DataFrame(rows, schema=schema)


def post_start_evidence(listings: pl.DataFrame) -> pl.DataFrame:
    """Earliest evidence, from *any* snapshot, that an event had started: a listed start at or
    before the snapshot showing it. Used only to exclude decisions at or after that instant."""
    started = listings.filter(pl.col("commence_utc") <= pl.col("snapshot_utc"))
    return started.group_by("provider_event_id").agg(
        pl.col("commence_utc").min().alias("started_by_utc")
    )


def apply_validity_censoring(population: pl.DataFrame, listings: pl.DataFrame) -> pl.DataFrame:
    evidence = post_start_evidence(listings)
    return (
        population.join(evidence, on="provider_event_id", how="left")
        .with_columns(
            pl.when(
                (pl.col("status") == "in_population")
                & pl.col("started_by_utc").is_not_null()
                & (pl.col("started_by_utc") <= pl.col("decision_utc"))
            )
            .then(pl.lit("started_before_decision"))
            .otherwise(pl.col("status"))
            .alias("status")
        )
        .drop("started_by_utc")
    )


def resolve_identity(population: pl.DataFrame, links: pl.DataFrame) -> pl.DataFrame:
    """Point-in-time identity: ``links`` maps provider events to canonical ``match_key``.

    A match is decided at its *first* decision with any in-population claimant. If exactly one
    provider event claims it there, that event is ``unique``. If two or more do, every claimant is
    an ``identity_conflict`` and the match is excluded for good: no later decision can rescue it.
    Claims at later decisions are ``already_assigned``. ``links`` rows with status ``linked`` or
    ``duplicate_provider_event`` carry a ``match_key``; uniqueness is judged per decision, among the
    events listed at that moment, so a store-wide duplicate whose twin is not listed then is unique.
    Nothing is merged and no claimant is chosen.
    """
    pop = population.filter(pl.col("status") == "in_population").join(
        links.select("provider_event_id", "match_key", "link_status"),
        on="provider_event_id",
        how="left",
    )
    claimable = (
        pl.col("link_status").is_in(["linked", "duplicate_provider_event"])
        & pl.col("match_key").is_not_null()
    )
    pop = pop.with_columns(claimable.alias("_claimable"))
    claims = pop.filter(pl.col("_claimable"))
    first = claims.group_by("match_key").agg(pl.col("decision_utc").min().alias("_first"))
    at_first = (
        claims.join(first, on="match_key")
        .filter(pl.col("decision_utc") == pl.col("_first"))
        .group_by("match_key")
        .agg(pl.len().alias("_n_first"))
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
        .drop("_claimable", "_first", "_n_first")
    )


def day_anchor_manifest(
    *,
    name: str,
    sport_key: str,
    days: list[date],
    local_decision_time: time = time(10, 0),
    tolerance_minutes: int = 10,
    markets: tuple[str, ...] = ("h2h",),
    regions: tuple[str, ...] = ("eu",),
    stage_ceiling: int = 130,
) -> StageManifest:
    """Acquisition manifest for candidate A2: one historical odds request per local day at
    ``T_d``, fixed *before* any data are seen (no discovery, no schedule-driven timing).

    Each request carries one sentinel ``PlannedEvent`` whose decision is ``T_d``, so the existing
    ledger-enforced runner stops if the provider returns a snapshot older than ``T_d - tolerance``
    or later than ``T_d``. Requests are paid with ``run_stage`` only after owner approval.
    """
    tz = TOURNAMENT_TZ[sport_key]
    cost = cost_historical_odds(markets, regions)
    requests = []
    for i, day in enumerate(sorted(days), start=1):
        decision = local_instant(day, local_decision_time, tz)
        sentinel = PlannedEvent(
            provider_event_id=f"__day_anchor__{day.isoformat()}",
            scheduled_start_utc=iso(decision + timedelta(hours=1)),
            decision_utc=iso(decision),
            discovered_later=False,
        )
        requests.append(
            PlannedRequest(
                request_id=f"{name}-{i:02d}",
                sport_key=sport_key,
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
        sport_key=sport_key,
        discovery_capture_id="none: fixed local clock (DRAFT v2 candidate A2)",
        discovery_snapshot_utc=requests[0].request_date_utc if requests else "",
        lead_minutes=60.0,
        max_gap_minutes=float(tolerance_minutes),
        cadence_minutes=10,
        grid_offset_minutes=5,
        markets=markets,
        regions=regions,
        stage_ceiling=stage_ceiling,
        planned_credits=total,
        requests=tuple(requests),
    )
