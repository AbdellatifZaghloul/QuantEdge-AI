"""Fail-closed safety wrapper for paid V2 acquisition (outside the frozen implementation).

``market-benchmark-v2`` pins ``odds/stage.py`` and ``odds/v2_draft.py`` by SHA-256, so they are not
edited. This module adds programme-level controls *around* them; it changes no scientific rule:

1. the frozen protocol, its calendar and the pinned implementation files are re-verified;
2. spending is accounted across **all** V2 stages against the frozen programme ceiling;
3. no tournament day may be claimed by two stages or bought twice;
4. every manifest must match the frozen calendar, every ledger entry a stored capture, every lock
   its manifest and ledger, and every tranche plan its manifest;
5. a historical capture stored on or after the freeze date that no ledger accounts for blocks
   further paid V2 calls (it would otherwise enter the as-of snapshots unbudgeted).

Every check runs before any client is created, so a refused call never reaches the provider.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from quantedge.odds.stage import (
    LOCK_FILE,
    TRANCHE_FILE,
    StageManifest,
    is_sentinel,
    read_ledger,
    run_stage,
)
from quantedge.odds.store import CaptureStore, StoreIntegrityError
from quantedge.odds.theoddsapi import TheOddsApiClient
from quantedge.odds.v2_draft import (
    EditionCalendar,
    V2IntegrationError,
    load_calendar,
    manifest_days,
    read_tranche_plan,
    run_tranche,
)

FROZEN_V2_RELPATH = Path("configs/preregistration/market_benchmark_v2.yaml")
FROZEN_V2_SHA256 = "ad319ba7384cd95d5b1c2c45fd5c3c058af6af41c6e95c3926f253e990fae70c"
CALENDAR_RELPATH = Path("src/quantedge/reference/v2_draft_edition_calendar.csv")


class V2GuardError(RuntimeError):
    """A paid V2 call is refused: the programme state is unsafe or inconsistent."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _raw_sha(path: Path) -> str:
    """Hash of the exact bytes, as written by the frozen ``write_lock``."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_frozen_v2(repo_root: Path) -> dict[str, Any]:
    """Re-verify the frozen protocol, its calendar and the pinned implementation; return it."""
    path = repo_root / FROZEN_V2_RELPATH
    if not path.exists():
        raise V2GuardError(f"frozen protocol missing: {path}")
    if _sha(path) != FROZEN_V2_SHA256:
        raise V2GuardError("market-benchmark-v2 fingerprint mismatch: the frozen protocol changed")
    protocol: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    if protocol.get("status") != "FROZEN" or protocol.get("protocol_id") != "market-benchmark-v2":
        raise V2GuardError("market-benchmark-v2 is not marked FROZEN")
    if _sha(repo_root / CALENDAR_RELPATH) != protocol["population"]["calendar_sha256"]:
        raise V2GuardError("calendar fingerprint mismatch with the frozen protocol")
    for rel, digest in protocol["implementation"]["files_sha256"].items():
        if _sha(repo_root / rel) != digest:
            raise V2GuardError(f"frozen implementation changed: {rel}")
    return protocol


@dataclass
class ProgrammeState:
    ceiling: int
    spent: int = 0
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    claimed: dict[tuple[str, date], list[str]] = field(default_factory=lambda: defaultdict(list))
    paid: dict[tuple[str, date], list[str]] = field(default_factory=lambda: defaultdict(list))
    ledger_captures: set[str] = field(default_factory=set)
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def remaining(self) -> int:
        return self.ceiling - self.spent


def _edition_of(manifest: StageManifest, calendars: dict[str, EditionCalendar]) -> EditionCalendar:
    ids = {
        e.provider_event_id.split("__day_anchor__", 1)[1].split(":", 1)[0]
        for r in manifest.requests
        for e in r.events
        if is_sentinel(e.provider_event_id)
    }
    if len(ids) != 1 or next(iter(ids)) not in calendars:
        raise V2GuardError(f"{manifest.name}: cannot attribute the manifest to one frozen edition")
    return calendars[next(iter(ids))]


def is_v2_manifest(manifest: StageManifest) -> bool:
    return any(is_sentinel(e.provider_event_id) for r in manifest.requests for e in r.events)


def _verify_stage_files(
    stage_dir: Path, name: str, *, ledger_captures: list[str], verified_sha: dict[str, str]
) -> None:
    """Tranche plan bound to the manifest; lock bound to the manifest, ledger and captures.

    ``verified_sha`` maps capture id -> SHA-256 of a body re-hashed on read; every locked capture
    must be present with exactly the hash recorded in the lock.
    """
    if (stage_dir / TRANCHE_FILE).exists():
        try:
            read_tranche_plan(stage_dir)
        except V2IntegrationError as exc:
            raise V2GuardError(f"{name}: {exc}") from exc
    if (stage_dir / LOCK_FILE).exists():
        lock = json.loads((stage_dir / LOCK_FILE).read_text(encoding="utf-8"))
        if lock["manifest_sha256"] != _raw_sha(stage_dir / "manifest.json"):
            raise V2GuardError(f"{name}: manifest changed after the lock")
        if ledger_captures and lock["ledger_sha256"] != _raw_sha(stage_dir / "ledger.jsonl"):
            raise V2GuardError(f"{name}: ledger changed after the lock")
        if [c["capture_id"] for c in lock["captures"]] != ledger_captures:
            raise V2GuardError(f"{name}: lock does not list the ledger's captures")
        for c in lock["captures"]:
            if verified_sha.get(c["capture_id"]) != c["sha256"]:
                raise V2GuardError(f"{name}: locked capture {c['capture_id']} fails its SHA-256")


def programme_state(
    stages_root: Path, store: CaptureStore, *, ceiling: int, freeze_date: date
) -> ProgrammeState:
    """Account every V2 stage; raise on any inconsistency (fail closed)."""
    calendars = load_calendar()
    state = ProgrammeState(ceiling=ceiling)
    try:  # re-hash every stored body; a single mismatch refuses the whole programme
        verified = {rec.capture_id: rec for rec, _ in store.iter_captures()}
    except StoreIntegrityError as exc:
        raise V2GuardError(f"capture store integrity: {exc}") from exc
    records = verified
    verified_sha = {cid: rec.sha256 for cid, rec in verified.items()}
    all_ledger_captures: set[str] = set()
    for stage_dir in sorted(p for p in stages_root.iterdir() if (p / "manifest.json").exists()):
        name = stage_dir.name
        manifest = StageManifest.from_json(
            (stage_dir / "manifest.json").read_text(encoding="utf-8")
        )
        ledger = read_ledger(stage_dir / "ledger.jsonl")
        all_ledger_captures |= {e.capture_id for e in ledger}
        if not is_v2_manifest(manifest):
            state.skipped[name] = "not a V2 day-anchor manifest"
            continue
        if not manifest.strict_snapshot_window:
            if ledger:
                raise V2GuardError(f"{name}: legacy V2 manifest has paid entries")
            state.skipped[name] = "legacy V2 manifest (refused by the runner)"
            continue
        calendar = _edition_of(manifest, calendars)
        try:
            days = manifest_days(manifest, calendar)
        except V2IntegrationError as exc:
            raise V2GuardError(f"{name}: {exc}") from exc
        day_of = dict(zip((r.request_id for r in manifest.requests), days, strict=True))
        for d in days:
            state.claimed[(calendar.edition_id, d)].append(name)
        charged = 0
        for e in ledger:
            if e.request_id not in day_of:
                raise V2GuardError(f"{name}: ledger entry {e.request_id} is not in the manifest")
            rec = records.get(e.capture_id)
            if rec is None or rec.credits_last != e.credits_charged:
                raise V2GuardError(f"{name}: ledger entry {e.request_id} does not match the store")
            charged += e.credits_charged
            state.paid[(calendar.edition_id, day_of[e.request_id])].append(name)
            state.ledger_captures.add(e.capture_id)
        if ledger and ledger[-1].cumulative_credits != charged:
            raise V2GuardError(f"{name}: ledger cumulative total does not reconcile")
        _verify_stage_files(
            stage_dir,
            name,
            ledger_captures=[e.capture_id for e in ledger],
            verified_sha=verified_sha,
        )
        state.spent += charged
        state.stages[name] = {
            "edition": calendar.edition_id,
            "days": len(days),
            "paid_requests": len(ledger),
            "credits": charged,
            "locked": (stage_dir / LOCK_FILE).exists(),
        }
    for key, stages in state.paid.items():
        if len(stages) > 1:
            raise V2GuardError(f"{key[0]} {key[1]}: paid by more than one request")
    unaccounted = [
        r.capture_id
        for r in records.values()
        if "/historical/" in r.endpoint
        and r.captured_at_utc[:10] >= freeze_date.isoformat()
        and r.capture_id not in all_ledger_captures
    ]
    if unaccounted:
        raise V2GuardError(
            f"{len(unaccounted)} historical capture(s) after the freeze have no ledger"
        )
    if state.spent > ceiling:
        raise V2GuardError(f"V2 spending {state.spent} already exceeds the ceiling {ceiling}")
    return state


def preflight(
    stage_dir: Path,
    *,
    repo_root: Path,
    store: CaptureStore,
    request_ids: frozenset[str] | None = None,
    ceiling: int | None = None,
) -> dict[str, Any]:
    """Refuse a paid V2 call unless the whole programme is consistent and it fits the ceiling."""
    protocol = verify_frozen_v2(repo_root)
    cap = int(protocol["acquisition"]["programme_ceiling_credits"]) if ceiling is None else ceiling
    state = programme_state(
        stage_dir.parent,
        store,
        ceiling=cap,
        freeze_date=date.fromisoformat(str(protocol["frozen_on"])),
    )
    name = stage_dir.name
    if name not in state.stages:
        raise V2GuardError(
            f"{name}: not an accountable V2 stage ({state.skipped.get(name, 'unknown')})"
        )
    manifest = StageManifest.from_json((stage_dir / "manifest.json").read_text(encoding="utf-8"))
    calendar = _edition_of(manifest, load_calendar())
    done = {e.request_id for e in read_ledger(stage_dir / "ledger.jsonl")}
    wanted = [
        (r, d)
        for r, d in zip(manifest.requests, manifest_days(manifest, calendar), strict=True)
        if r.request_id not in done and (request_ids is None or r.request_id in request_ids)
    ]
    for _req, d in wanted:
        key = (calendar.edition_id, d)
        others = [s for s in state.claimed[key] if s != name]
        if others or state.paid.get(key):
            raise V2GuardError(
                f"{key[0]} {d}: already claimed or paid by {others or state.paid[key]}"
            )
    cost = sum(r.credits for r, _ in wanted)
    if state.spent + cost > cap:
        raise V2GuardError(
            f"programme ceiling: spent {state.spent} + this call {cost} > {cap} credits"
        )
    return {
        "programme_ceiling": cap,
        "spent_before": state.spent,
        "this_call_max": cost,
        "remaining_after": cap - state.spent - cost,
        "stages": state.stages,
        "skipped_stages": state.skipped,
    }


def guarded_stage_run(
    stage_dir: Path,
    *,
    repo_root: Path,
    store: CaptureStore,
    client: TheOddsApiClient | None = None,
    execute: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    summary = preflight(stage_dir, repo_root=repo_root, store=store)
    manifest = StageManifest.from_json((stage_dir / "manifest.json").read_text(encoding="utf-8"))
    report = run_stage(
        manifest,
        store=store,
        ledger_path=stage_dir / "ledger.jsonl",
        client=client,
        execute=execute,
    )
    return summary, report


def guarded_tranche_run(
    stage_dir: Path,
    number: int,
    *,
    repo_root: Path,
    store: CaptureStore,
    client: TheOddsApiClient | None = None,
    execute: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tranche = {t.number: t for t in read_tranche_plan(stage_dir)}.get(number)
    if tranche is None:
        raise V2GuardError(f"no tranche {number}")
    summary = preflight(
        stage_dir, repo_root=repo_root, store=store, request_ids=frozenset(tranche.request_ids)
    )
    report = run_tranche(stage_dir, number, store=store, client=client, execute=execute)
    return summary, report


def v2_window_conflict(sport_key: str, when: date) -> str | None:
    """Edition whose acquisition window (first day - 2 .. last day + 2) contains ``when``."""
    for cal in load_calendar().values():
        if cal.sport_key == sport_key and (cal.first_day.toordinal() - 2) <= when.toordinal() <= (
            cal.last_day.toordinal() + 2
        ):
            return cal.edition_id
    return None
