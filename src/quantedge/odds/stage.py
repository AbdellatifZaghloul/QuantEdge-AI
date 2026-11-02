"""Staged historical acquisition: offline request planning and a ledger-enforced runner.

Planning (offline; no credentials, no network):

- ``decision(e) = scheduled start of e in the discovery snapshot - lead`` (60 min, frozen);
- the provider returns the closest snapshot at or before the requested ``date``; before Sept 2022
  snapshots sit on a 10-minute grid whose offset is inferred from the discovery snapshot;
- events are grouped in decision order and each group is requested at its *earliest* decision, so
  the returned snapshot can never be later than any decision it serves (no post-decision quotes);
- an event joins a group only while ``decision - expected snapshot <= max_gap_minutes``, leaving
  headroom for market ``last_update`` lag; freshness is still enforced by the frozen selector;
- events whose decision precedes the discovery snapshot are flagged ``discovered_later``: their
  admissibility must come from the decision-time snapshot itself, never from the later discovery.

Running (paid; only with explicit flags): each call is pre-checked against a cumulative append-only
credit ledger (stage ceiling, not per call), the capture store (already captured snapshots are never
re-bought) and the provider's remaining quota; the run stops on any unexpected cost, quota drift,
returned timestamp or error. Without ``execute=True`` nothing is requested.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from quantedge.odds.provider import NormalizedEvent
from quantedge.odds.selector import SelectorConfig, decisions_from_schedule, select_quotes
from quantedge.odds.store import CaptureStore
from quantedge.odds.theoddsapi import TheOddsApiClient, cost_historical_odds, parse_utc

# Absolute owner-approved ceiling for one stage. Any contingency above the *planned* total needs a
# fresh owner decision: the runner's cumulative cap is the planned total, never this number.
ABSOLUTE_STAGE_CEILING = 130


class StagePlanError(ValueError):
    """A plan would violate its timing or budget constraints."""


class StageRunStoppedError(RuntimeError):
    """A pre-call or post-call check failed; nothing further is requested."""


@dataclass(frozen=True)
class PlannedEvent:
    provider_event_id: str
    scheduled_start_utc: str
    decision_utc: str
    discovered_later: bool


@dataclass(frozen=True)
class PlannedRequest:
    request_id: str
    sport_key: str
    request_date_utc: str  # the ``date`` parameter: earliest decision in the group
    expected_snapshot_utc: str  # grid point at or before the request date
    credits: int
    events: tuple[PlannedEvent, ...]


@dataclass(frozen=True)
class StageManifest:
    name: str
    sport_key: str
    discovery_capture_id: str
    discovery_snapshot_utc: str
    lead_minutes: float
    max_gap_minutes: float
    cadence_minutes: int
    grid_offset_minutes: int
    markets: tuple[str, ...]
    regions: tuple[str, ...]
    stage_ceiling: int
    planned_credits: int
    requests: tuple[PlannedRequest, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"

    @staticmethod
    def from_json(text: str) -> StageManifest:
        raw = json.loads(text)
        raw["requests"] = tuple(
            PlannedRequest(**{**r, "events": tuple(PlannedEvent(**e) for e in r["events"])})
            for r in raw["requests"]
        )
        raw["markets"] = tuple(raw["markets"])
        raw["regions"] = tuple(raw["regions"])
        return StageManifest(**raw)


def iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc(text: str) -> datetime:
    parsed = parse_utc(text)
    if parsed is None:
        raise StagePlanError("empty timestamp")
    return parsed


def grid_floor(t: datetime, *, cadence_minutes: int, offset_minutes: int) -> datetime:
    """Latest grid point ``offset + k * cadence`` (minutes past 00:00 UTC) at or before ``t``."""
    t = t.astimezone(UTC)
    midnight = t.replace(hour=0, minute=0, second=0, microsecond=0)
    k = ((t - midnight).total_seconds() / 60 - offset_minutes) // cadence_minutes
    return midnight + timedelta(minutes=offset_minutes + k * cadence_minutes)


def infer_grid_offset(snapshot: datetime, cadence_minutes: int) -> int:
    snapshot = snapshot.astimezone(UTC)
    if snapshot.second or snapshot.microsecond:
        raise StagePlanError("discovery snapshot is not on a whole minute; cannot infer the grid")
    return (snapshot.hour * 60 + snapshot.minute) % cadence_minutes


def returned_snapshots(store: CaptureStore, sport_key: str) -> dict[str, str]:
    """Provider snapshot timestamp -> capture id for every stored historical odds capture."""
    out: dict[str, str] = {}
    for record, capture in store.iter_captures():  # re-verifies every body's SHA-256
        if record.competition_key != sport_key or "/historical/" not in record.endpoint:
            continue
        stamp = json.loads(capture.body.decode("utf-8")).get("timestamp")
        if isinstance(stamp, str) and (parsed := parse_utc(stamp)) is not None:
            out.setdefault(iso(parsed), record.capture_id)
    return out


def plan_stage(
    *,
    name: str,
    sport_key: str,
    discovery_capture_id: str,
    discovery_snapshot: datetime,
    events: Sequence[NormalizedEvent],
    already_captured: Sequence[str] = (),
    lead_minutes: float = 60.0,
    max_gap_minutes: float = 15.0,
    cadence_minutes: int = 10,
    markets: tuple[str, ...] = ("h2h",),
    regions: tuple[str, ...] = ("eu",),
    stage_ceiling: int = ABSOLUTE_STAGE_CEILING,
) -> StageManifest:
    """Group pre-match events of one discovery snapshot into decision-time snapshot requests.

    ``events`` must come from the discovery snapshot only (its schedule information); no quote,
    price or outcome is used. ``already_captured`` holds returned snapshot timestamps already in the
    store: a group whose expected snapshot is among them is a duplicate and is refused, because the
    plan should then be built around the existing capture instead of buying it again.
    """
    if stage_ceiling > ABSOLUTE_STAGE_CEILING:
        raise StagePlanError(f"stage ceiling {stage_ceiling} > approved {ABSOLUTE_STAGE_CEILING}")
    if max_gap_minutes < cadence_minutes:
        raise StagePlanError("max gap must be at least one snapshot interval")
    offset = infer_grid_offset(discovery_snapshot, cadence_minutes)
    cost = cost_historical_odds(markets, regions)
    gap = timedelta(minutes=max_gap_minutes)
    pre_match = sorted(
        (
            e
            for e in events
            if e.competition_key == sport_key and e.commence_time_utc > discovery_snapshot
        ),
        key=lambda e: (e.commence_time_utc, e.provider_event_id),
    )
    if len({e.provider_event_id for e in pre_match}) != len(pre_match):
        raise StagePlanError("duplicate provider event ids in the discovery snapshot")
    groups: list[tuple[datetime, datetime, list[PlannedEvent]]] = []
    for e in pre_match:
        decision = e.commence_time_utc - timedelta(minutes=lead_minutes)
        planned = PlannedEvent(
            provider_event_id=e.provider_event_id,
            scheduled_start_utc=iso(e.commence_time_utc),
            decision_utc=iso(decision),
            discovered_later=decision < discovery_snapshot,
        )
        if groups and decision - groups[-1][1] <= gap:
            groups[-1][2].append(planned)
        else:
            snap = grid_floor(decision, cadence_minutes=cadence_minutes, offset_minutes=offset)
            groups.append((decision, snap, [planned]))
    captured = set(already_captured)
    requests: list[PlannedRequest] = []
    for i, (request_at, snap, members) in enumerate(groups, start=1):
        if iso(snap) in captured:
            raise StagePlanError(f"group {i} would re-buy the stored snapshot {iso(snap)}")
        for ev in members:  # invariants, re-checked rather than assumed
            decision = _utc(ev.decision_utc)
            if not snap <= request_at <= decision or decision - snap > gap:
                raise StagePlanError(f"group {i}: snapshot/decision ordering violated")
        requests.append(
            PlannedRequest(
                request_id=f"{name}-{i:02d}",
                sport_key=sport_key,
                request_date_utc=iso(request_at),
                expected_snapshot_utc=iso(snap),
                credits=cost,
                events=tuple(members),
            )
        )
    if len({r.expected_snapshot_utc for r in requests}) != len(requests):
        raise StagePlanError("two requests map to the same snapshot")
    total = sum(r.credits for r in requests)
    if total > stage_ceiling:
        raise StagePlanError(f"plan needs {total} credits, above the stage ceiling {stage_ceiling}")
    return StageManifest(
        name=name,
        sport_key=sport_key,
        discovery_capture_id=discovery_capture_id,
        discovery_snapshot_utc=iso(discovery_snapshot),
        lead_minutes=lead_minutes,
        max_gap_minutes=max_gap_minutes,
        cadence_minutes=cadence_minutes,
        grid_offset_minutes=offset,
        markets=markets,
        regions=regions,
        stage_ceiling=stage_ceiling,
        planned_credits=total,
        requests=tuple(requests),
    )


def manifest_summary(manifest: StageManifest) -> list[dict[str, Any]]:
    """Per-request rows without provider event ids (safe to print or document)."""
    rows, cumulative = [], 0
    for r in manifest.requests:
        cumulative += r.credits
        decisions = [_utc(e.decision_utc) for e in r.events]
        snap = _utc(r.expected_snapshot_utc)
        rows.append(
            {
                "request_id": r.request_id,
                "request_date_utc": r.request_date_utc,
                "expected_snapshot_utc": r.expected_snapshot_utc,
                "events": len(r.events),
                "discovered_later": sum(e.discovered_later for e in r.events),
                "first_decision_utc": iso(min(decisions)),
                "last_decision_utc": iso(max(decisions)),
                "max_gap_minutes": max((d - snap).total_seconds() / 60 for d in decisions),
                "credits": r.credits,
                "cumulative_credits": cumulative,
            }
        )
    return rows


# ------------------------------------------------------------------------------------- runner


@dataclass(frozen=True)
class LedgerEntry:
    request_id: str
    request_date_utc: str
    expected_snapshot_utc: str
    returned_snapshot_utc: str | None
    credits_charged: int
    credits_remaining: int | None
    cumulative_credits: int
    capture_id: str
    events_planned: int
    events_present: int
    recorded_at_utc: str


def read_ledger(path: Path) -> list[LedgerEntry]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [LedgerEntry(**json.loads(line)) for line in lines if line.strip()]


def _append_ledger(path: Path, entry: LedgerEntry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(asdict(entry), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def last_known_quota(store: CaptureStore) -> int | None:
    """Provider ``x-requests-remaining`` recorded on the most recent stored capture, if any."""
    known = [r for r in store.records() if r.credits_remaining is not None]
    return max(known, key=lambda r: r.captured_at_utc).credits_remaining if known else None


def _check_client(client: TheOddsApiClient | None, manifest: StageManifest, ceiling: int) -> None:
    if client is None:
        raise StageRunStoppedError("execute=True requires a configured client")
    if client.config.max_retries != 0:
        raise StageRunStoppedError("paid stage calls require max_retries=0 (no silent re-charges)")
    if client.config.max_credits_per_run > ceiling:
        raise StageRunStoppedError("client run budget exceeds the stage total")
    if (tuple(client.config.markets), tuple(client.config.regions)) != (
        manifest.markets,
        manifest.regions,
    ):
        raise StageRunStoppedError("client markets/regions differ from the manifest")


def _snapshot_facts(body_bytes: bytes) -> tuple[datetime | None, set[str]]:
    """Returned snapshot timestamp and the provider event ids present in a historical body."""
    body = json.loads(body_bytes.decode("utf-8"))
    if not isinstance(body, dict):
        return None, set()
    stamp = body.get("timestamp")
    data = body.get("data")
    ids = (
        {str(e.get("id")) for e in data if isinstance(e, dict)} if isinstance(data, list) else set()
    )
    return (parse_utc(stamp) if isinstance(stamp, str) else None), ids


def _verify_call(
    req: PlannedRequest,
    entry: LedgerEntry,
    *,
    credits_header: bool,
    previous_remaining: int | None,
    gap: timedelta,
) -> None:
    if not credits_header or entry.credits_charged != req.credits:
        raise StageRunStoppedError(
            f"{req.request_id}: charged {entry.credits_charged}, expected {req.credits}"
        )
    if (
        previous_remaining is not None
        and entry.credits_remaining != previous_remaining - entry.credits_charged
    ):
        raise StageRunStoppedError(
            f"{req.request_id}: quota moved from {previous_remaining} to "
            f"{entry.credits_remaining}, not by {entry.credits_charged}"
        )
    returned = parse_utc(entry.returned_snapshot_utc)
    if returned is None or returned > _utc(req.request_date_utc):
        raise StageRunStoppedError(f"{req.request_id}: missing or post-request snapshot timestamp")
    if any(_utc(e.decision_utc) - returned > gap for e in req.events):
        raise StageRunStoppedError(
            f"{req.request_id}: returned {iso(returned)} is older than planned "
            f"({req.expected_snapshot_utc}); grid assumption failed"
        )


def run_stage(
    manifest: StageManifest,
    *,
    store: CaptureStore,
    ledger_path: Path,
    client: TheOddsApiClient | None = None,
    execute: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> list[dict[str, Any]]:
    """Walk the manifest in order; dry run unless ``execute`` (and an approved client) is given.

    Pre-call checks: not already in the ledger or store; cumulative spend + cost <= planned total
    (<= stage ceiling <= 130); client has no automatic retries and a run budget no larger than the
    stage total; the last known remaining quota (ledger, else the newest stored capture) covers the
    cost. Post-call checks (any failure stops
    the run *after* the paid body is stored and the ledger written): charged == planned cost;
    remaining quota fell by exactly the charge; returned snapshot <= requested date and within the
    gap bound of every decision in the group.
    """
    now = clock or (lambda: datetime.now(tz=UTC))
    ceiling = min(manifest.planned_credits, manifest.stage_ceiling, ABSOLUTE_STAGE_CEILING)
    ledger = read_ledger(ledger_path)
    spent = ledger[-1].cumulative_credits if ledger else 0
    remaining = ledger[-1].credits_remaining if ledger else last_known_quota(store)
    done = {e.request_id for e in ledger}
    stored = returned_snapshots(store, manifest.sport_key)
    gap = timedelta(minutes=manifest.max_gap_minutes)
    if execute:
        _check_client(client, manifest, ceiling)
    report: list[dict[str, Any]] = []
    for req in manifest.requests:
        row: dict[str, Any] = {
            "request_id": req.request_id,
            "request_date_utc": req.request_date_utc,
            "expected_snapshot_utc": req.expected_snapshot_utc,
            "events": len(req.events),
            "credits": req.credits,
        }
        if req.request_id in done or req.expected_snapshot_utc in stored:
            report.append(row | {"action": "skip_already_captured", "cumulative": spent})
            continue
        if spent + req.credits > ceiling:
            raise StageRunStoppedError(
                f"{req.request_id}: cumulative credits would reach {spent + req.credits} "
                f"(> stage total {ceiling})"
            )
        if remaining is not None and remaining < req.credits:
            raise StageRunStoppedError(
                f"{req.request_id}: provider quota {remaining} < {req.credits}"
            )
        if not execute:
            spent += req.credits
            report.append(row | {"action": "would_request", "cumulative": spent})
            continue
        assert client is not None
        capture = client.historical_odds(req.sport_key, _utc(req.request_date_utc), approved=True)
        record, _ = store.append(capture)  # persist first: a paid body is never lost
        returned, ids = _snapshot_facts(capture.body)
        charged = capture.credits_last if capture.credits_last is not None else req.credits
        spent += charged
        entry = LedgerEntry(
            request_id=req.request_id,
            request_date_utc=req.request_date_utc,
            expected_snapshot_utc=req.expected_snapshot_utc,
            returned_snapshot_utc=iso(returned) if returned else None,
            credits_charged=charged,
            credits_remaining=capture.credits_remaining,
            cumulative_credits=spent,
            capture_id=record.capture_id,
            events_planned=len(req.events),
            events_present=sum(e.provider_event_id in ids for e in req.events),
            recorded_at_utc=iso(now()),
        )
        _append_ledger(ledger_path, entry)
        report.append(row | {"action": "requested", "cumulative": spent, **asdict(entry)})
        _verify_call(
            req,
            entry,
            credits_header=capture.credits_last is not None,
            previous_remaining=remaining,
            gap=gap,
        )
        remaining = capture.credits_remaining
    return report


# -------------------------------------------------------------------------------- go / no-go

# Fixed before any stage data exists (outcome-blind; docs/reviews/historical-odds-four-slam-probe.md
# section 5). Denominator: every planned event, so a planned event missing from its snapshot counts
# as a failure rather than silently leaving the population.
GO_CRITERIA = {
    "share_events_ge1_available": 0.80,
    "share_events_ge2_available": 0.70,
    "median_quote_age_minutes_max": 20.0,
    "share_events_linked": 0.90,
}
PROBE_QUARANTINE_REASONS = frozenset({"price_not_above_one"})  # reason classes seen in the probe
SENSITIVITY_FRESHNESS_MINUTES = (15.0, 60.0)  # market-benchmark-v1 sensitivities; reported only


def _median(values: pl.Series) -> float | None:
    values = values.drop_nulls()
    return float(values.cast(pl.Float64).median()) if values.len() else None  # type: ignore[arg-type]


def _per_event_availability(events: pl.DataFrame, priced: pl.DataFrame) -> pl.DataFrame:
    counts = priced.group_by("event_id").agg(
        (pl.col("status") == "available").sum().alias("n_available")
    )
    return (
        events.select("event_id", "provider_event_id", "commence_time_earliest_utc")
        .join(counts, on="event_id", how="left")
        .with_columns(pl.col("n_available").fill_null(0))
    )


def stage_audit(
    manifest: StageManifest,
    *,
    events: pl.DataFrame,
    quotes: pl.DataFrame,
    quarantine: pl.DataFrame,
    ledger: Sequence[LedgerEntry],
    links: pl.DataFrame | None = None,
    freshness_minutes: float = 30.0,
) -> dict[str, Any]:
    """Outcome-blind go/no-go evaluation of a completed stage under the frozen timing rule.

    Decisions use the frozen anchor (earliest scheduled start ever observed - lead), not the
    manifest's planning anchor; events whose anchor moved are reported.

    Quote-age populations (all ages are the selector's ``staleness_minutes`` at decision):

    - ``median_quote_age_minutes`` (gates c3, as pre-specified before any stage data): each
      bookmaker's newest admissible complete h2h quote, *available or stale*. It is the
      conservative choice: adding stale quotes can only raise the median, and it is not truncated
      at the freshness limit;
    - ``median_quote_age_available_minutes``: only quotes that pass freshness (the quotes that
      would enter probabilities); bounded by the freshness limit, reported, not gating;
    - ``median_event_quote_age_minutes``: median over events of each event's median available
      age, so events with many bookmakers do not dominate; reported, not gating.
    """
    planned = {e.provider_event_id: e for r in manifest.requests for e in r.events}
    ev = events.filter(
        (pl.col("competition_key") == manifest.sport_key)
        & pl.col("provider_event_id").is_in(list(planned))
    )
    decisions = decisions_from_schedule(ev, manifest.lead_minutes)
    selected = select_quotes(
        quotes, decisions, SelectorConfig(max_staleness_minutes=freshness_minutes)
    )
    priced = selected.filter(pl.col("bookmaker_key").is_not_null())
    per_event = _per_event_availability(ev, priced)
    later = {pid for pid, e in planned.items() if e.discovered_later}
    n = len(planned)  # planned events absent from every snapshot count as unavailable

    def share(frame: pl.DataFrame, k: int) -> float:
        return frame.filter(pl.col("n_available") >= k).height / n if n else 0.0

    moved = sum(
        iso(row["commence_time_earliest_utc"])
        != planned[row["provider_event_id"]].scheduled_start_utc
        for row in ev.iter_rows(named=True)
    )
    ages = priced["staleness_minutes"].drop_nulls()
    available = priced.filter(pl.col("status") == "available")
    event_ages = available.group_by("event_id").agg(pl.col("staleness_minutes").median())
    sensitivity = {}
    for minutes in SENSITIVITY_FRESHNESS_MINUTES:
        alt = select_quotes(quotes, decisions, SelectorConfig(max_staleness_minutes=minutes))
        alt_events = _per_event_availability(ev, alt.filter(pl.col("bookmaker_key").is_not_null()))
        sensitivity[f"{minutes:g}"] = {"ge1": share(alt_events, 1), "ge2": share(alt_events, 2)}
    flags = quotes.filter(pl.col("event_id").is_in(ev["event_id"].to_list()))["quality_flags"]
    reasons = set(quarantine["reason_code"].unique().to_list()) if quarantine.height else set()
    linked = (
        links.filter(
            pl.col("event_id").is_in(ev["event_id"].to_list()) & (pl.col("status") == "linked")
        ).height
        if links is not None
        else None
    )
    later_frame = per_event.filter(pl.col("provider_event_id").is_in(list(later)))
    metrics: dict[str, Any] = {
        "planned_events": n,
        "events_seen": ev.height,
        "anchor_moved_events": moved,
        "share_events_ge1_available": share(per_event, 1),
        "share_events_ge2_available": share(per_event, 2),
        "median_quote_age_minutes": _median(ages),
        "median_quote_age_available_minutes": _median(available["staleness_minutes"]),
        "median_event_quote_age_minutes": _median(event_ages["staleness_minutes"]),
        "freshness_sensitivity": sensitivity,
        "anchor_moved_unavailable": per_event.filter(
            (pl.col("n_available") == 0)
            & pl.col("provider_event_id").is_in(
                [
                    row["provider_event_id"]
                    for row in ev.iter_rows(named=True)
                    if iso(row["commence_time_earliest_utc"])
                    != planned[row["provider_event_id"]].scheduled_start_utc
                ]
            )
        ).height,
        "discovered_later": {
            "planned": len(later),
            "ge1_available": later_frame.filter(pl.col("n_available") >= 1).height,
        },
        "returned_after_request": sum(
            e.returned_snapshot_utc is None or e.returned_snapshot_utc > e.request_date_utc
            for e in ledger
        ),
        "source_after_observation_quotes": int(
            flags.str.contains("source_after_observation").sum()
        ),
        "new_quarantine_reasons": sorted(reasons - PROBE_QUARANTINE_REASONS),
        "share_events_linked": linked / n if linked is not None and n else None,
        "credits_spent": ledger[-1].cumulative_credits if ledger else 0,
    }
    age = metrics["median_quote_age_minutes"]
    checks = {
        "c1_ge1_available": metrics["share_events_ge1_available"]
        >= GO_CRITERIA["share_events_ge1_available"],
        "c2_ge2_available": metrics["share_events_ge2_available"]
        >= GO_CRITERIA["share_events_ge2_available"],
        "c3_median_age": age is not None and age <= GO_CRITERIA["median_quote_age_minutes_max"],
        "c4_timestamps": metrics["returned_after_request"] == 0
        and metrics["source_after_observation_quotes"] == 0,
        "c5_linking": metrics["share_events_linked"] is not None
        and metrics["share_events_linked"] >= GO_CRITERIA["share_events_linked"],
        "c6_no_new_anomaly": not metrics["new_quarantine_reasons"],
        "within_ceiling": metrics["credits_spent"] <= manifest.planned_credits,
    }
    return {"metrics": metrics, "checks": checks, "go": all(checks.values())}
