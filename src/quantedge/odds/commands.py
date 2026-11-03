"""``quantedge odds ...`` commands. Chargeable calls need explicit flags and a credit ceiling."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import polars as pl
import typer
import yaml

from quantedge.canonical.build import load_tables, resolve_build_dir
from quantedge.core.dataview import DataView
from quantedge.core.splits import SplitConfig, load_split_config
from quantedge.curation.eligibility import build_eligibility
from quantedge.odds.baseline import (
    attach_participant_order,
    compare_on_identical_population,
    consensus,
    market_probabilities,
)
from quantedge.odds.coverage import assess_coverage
from quantedge.odds.identity import PlayerResolver, link_events, load_tournament_aliases
from quantedge.odds.pilot import pilot_population, run_pilot
from quantedge.odds.planner import build_plan
from quantedge.odds.preregistration import load_protocol
from quantedge.odds.schemas import validate_odds_table
from quantedge.odds.selector import SelectorConfig, decisions_from_schedule, select_quotes
from quantedge.odds.stage import (
    LOCK_FILE,
    StageManifest,
    StagePlanError,
    StageRunStoppedError,
    manifest_summary,
    plan_stage,
    read_ledger,
    returned_snapshots,
    run_stage,
    stage_audit,
)
from quantedge.odds.store import CaptureStore, Stream
from quantedge.odds.tables import build_odds_tables_with_quarantine
from quantedge.odds.theoddsapi import (
    PROVIDER,
    OddsApiConfig,
    OddsApiError,
    TheOddsApiClient,
    TheOddsApiNormalizer,
)
from quantedge.odds.v2_draft import (
    V2IntegrationError,
    check_tranche,
    day_anchor_manifest,
    identity_links,
    load_calendar,
    read_tranche_plan,
    schedule_listings,
    v2_audit,
    write_lock,
    write_tranche_plan,
)
from quantedge.odds.v2_guard import (
    V2GuardError,
    guarded_stage_run,
    guarded_tranche_run,
    is_v2_manifest,
    v2_window_conflict,
)
from quantedge.odds.v2_guard import (
    preflight as v2_preflight,
)

odds_app = typer.Typer(no_args_is_help=True, help="Odds infrastructure (Phase 3A).")

ConfigDir = Annotated[Path, typer.Option(help="Directory containing YAML configs.")]
DataRoot = Annotated[Path, typer.Option(help="Root of the gitignored data directory.")]


def _api_config(config_dir: Path, max_credits: int | None) -> OddsApiConfig:
    data: dict[str, Any] = yaml.safe_load((config_dir / "odds.yaml").read_text(encoding="utf-8"))
    config = OddsApiConfig.model_validate(data["the_odds_api"])
    if max_credits is not None:
        config = config.model_copy(update={"max_credits_per_run": max_credits})
    return config


def _store(data_root: Path, stream: Stream) -> CaptureStore:
    return CaptureStore(data_root, PROVIDER, TheOddsApiNormalizer.licence_class, stream)


@odds_app.command("coverage")
def coverage_cmd(
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
    snapshots_per_day: int = 1,
) -> None:
    """Documented historical coverage vs canonical matches, with a credit estimate (offline)."""
    tables = load_tables(resolve_build_dir(data_root / "processed"))
    splits = load_split_config(config_dir / "splits.yaml")
    eligibility = build_eligibility(tables["matches"], tables["tournaments"])
    api = _api_config(config_dir, None)
    report = assess_coverage(
        tables["matches"],
        tables["tournaments"],
        eligibility,
        development_end=splits.development_end,
        snapshots_per_day=snapshots_per_day,
        markets=api.markets,
        regions=api.regions,
    )
    typer.echo(json.dumps(report, indent=2, default=str))


@odds_app.command("sports")
def sports_cmd(config_dir: ConfigDir = Path("configs")) -> None:
    """List in-season and out-of-season sport keys (free endpoint; needs QE_ODDS_API_KEY)."""
    client = TheOddsApiClient(_api_config(config_dir, None))
    try:
        capture = client.sports()
    except OddsApiError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    keys = [
        s["key"] for s in json.loads(capture.body) if str(s.get("key", "")).startswith("tennis_atp")
    ]
    typer.echo(
        json.dumps({"tennis_atp_keys": keys, "credits_remaining": capture.credits_remaining})
    )


@odds_app.command("capture-live")
def capture_live_cmd(
    sport: Annotated[str, typer.Option(help="Sport key, e.g. tennis_atp_wimbledon.")],
    confirm_credit_use: Annotated[
        bool, typer.Option(help="Required: this call costs credits.")
    ] = False,
    max_credits: Annotated[int | None, typer.Option(help="Per-run credit ceiling.")] = None,
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
) -> None:
    """Forward capture of current pre-match odds into the append-only prospective store."""
    if not confirm_credit_use:
        typer.echo("refusing: pass --confirm-credit-use (markets x regions credits)", err=True)
        raise typer.Exit(code=2)
    client = TheOddsApiClient(_api_config(config_dir, max_credits))
    try:
        capture = client.live_odds(sport)
    except OddsApiError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    record, created = _store(data_root, "prospective").append(capture)
    typer.echo(
        json.dumps(
            {
                "capture_id": record.capture_id,
                "created": created,
                "credits_last": record.credits_last,
                "credits_remaining": record.credits_remaining,
            }
        )
    )


@odds_app.command("capture-historical")
def capture_historical_cmd(
    sport: Annotated[str, typer.Option(help="Sport key.")],
    at: Annotated[str, typer.Option(help="ISO-8601 UTC snapshot time, e.g. 2024-06-01T10:00:00Z.")],
    approve_paid_call: Annotated[
        bool, typer.Option(help="Owner approval for a paid call.")
    ] = False,
    max_credits: Annotated[int | None, typer.Option(help="Per-run credit ceiling.")] = None,
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
) -> None:
    """One historical snapshot (10 x markets x regions credits) into the historical store.

    Refused inside any frozen V2 edition window: those snapshots may only be bought through the
    guarded V2 manifests, otherwise they would enter V2 as-of snapshots outside the budget.
    """
    when = datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(UTC)
    edition = v2_window_conflict(sport, when.date())
    if edition is not None:
        typer.echo(f"refusing: {at} lies in the frozen V2 window of {edition}", err=True)
        raise typer.Exit(code=2)
    client = TheOddsApiClient(_api_config(config_dir, max_credits))
    try:
        capture = client.historical_odds(sport, when, approved=approve_paid_call)
    except OddsApiError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    record, created = _store(data_root, "historical").append(capture)
    typer.echo(
        json.dumps(
            {
                "capture_id": record.capture_id,
                "created": created,
                "credits_remaining": record.credits_remaining,
            }
        )
    )


@odds_app.command("build-tables")
def build_tables_cmd(
    stream: Annotated[str, typer.Option(help="prospective | historical")] = "prospective",
    data_root: DataRoot = Path("data"),
) -> None:
    """Normalise a capture store into odds_events / odds_quotes Parquet (store is untouched)."""
    result = build_odds_tables_with_quarantine(_store(data_root, stream), TheOddsApiNormalizer())  # type: ignore[arg-type]
    out = data_root / "processed" / "odds" / PROVIDER / stream  # gitignored (licensed data)
    out.mkdir(parents=True, exist_ok=True)
    result.events.write_parquet(out / "odds_events.parquet")
    result.quotes.write_parquet(out / "odds_quotes.parquet")
    result.quarantine.write_parquet(out / "odds_quote_quarantine.parquet")
    (out / "build_counts.json").write_text(
        json.dumps(result.counts, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    typer.echo(json.dumps({**result.counts, "out": str(out)}))


def identity_view(
    tables: dict[str, pl.DataFrame], splits: SplitConfig
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Matches and tournaments for identity linking, cut at the end of the inner folds.

    The odds pilot covers 2020-2022 only, so linking never needs (and never reads) the 2023-24
    late-check rows or the sealed holdout.
    """
    view = DataView(tables, splits)
    return (
        view.table("matches", end=splits.inner_end),
        view.table("tournaments", end=splits.inner_end),
    )


@odds_app.command("link")
def link_cmd(
    stream: Annotated[str, typer.Option(help="prospective | historical")] = "historical",
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
) -> None:
    """Link normalised events to canonical development matches (identity only; no outcomes)."""
    out = data_root / "processed" / "odds" / PROVIDER / stream
    events = pl.read_parquet(out / "odds_events.parquet")
    tables = load_tables(resolve_build_dir(data_root / "processed"))
    matches, tournaments = identity_view(tables, load_split_config(config_dir / "splits.yaml"))
    appearances = pl.concat(
        [
            matches.select(pl.col(c).alias("player_id"), "tournament_start_date")
            for c in ("player_a_id", "player_b_id")
        ]
    ).unique()
    resolver = PlayerResolver(tables["players"], appearances, PROVIDER)
    links = link_events(events, matches, tournaments, resolver, provider=PROVIDER)
    links = validate_odds_table("event_links", links) if links.height else links
    links.write_parquet(out / "event_links.parquet")
    summary = links.group_by("status").len().sort("status").to_dicts() if links.height else []
    typer.echo(json.dumps({"events": events.height, "by_status": summary}))


@odds_app.command("compare")
def compare_cmd(
    run_id: Annotated[
        str, typer.Option(help="Phase 2 inner-fold run whose test predictions to use.")
    ],
    stream: Annotated[str, typer.Option(help="Odds stream with linked events.")] = "historical",
    lead_minutes: float = 60.0,
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
    runs_root: Annotated[Path, typer.Option(help="Experiment registry root.")] = Path("runs"),
) -> None:
    """Market (de-vigged consensus) vs Phase 2 Elo on identical odds-covered inner-fold matches."""
    out = data_root / "processed" / "odds" / PROVIDER / stream
    needed = [
        out / f for f in ("odds_events.parquet", "odds_quotes.parquet", "event_links.parquet")
    ]
    if not all(p.exists() for p in needed):
        typer.echo(
            "no linked odds available for this stream; run capture/build-tables/link first",
            err=True,
        )
        raise typer.Exit(code=1)
    events, quotes, links = (pl.read_parquet(p) for p in needed)
    settings = yaml.safe_load((config_dir / "odds.yaml").read_text(encoding="utf-8"))["selector"]
    selected = attach_participant_order(
        select_quotes(
            quotes,
            decisions_from_schedule(events, lead_minutes),
            SelectorConfig.model_validate(settings),
        ),
        events,
    )
    probs = market_probabilities(selected, links)
    if probs.height == 0:
        typer.echo("no usable point-in-time market probabilities", err=True)
        raise typer.Exit(code=1)
    market = (
        consensus(probs, "p_proportional_a")
        .join(
            consensus(probs, "p_power_a").drop("n_bookmakers"),
            on=["match_key", "decision_time_utc"],
        )
        .join(
            consensus(probs, "p_shin_a").drop("n_bookmakers"), on=["match_key", "decision_time_utc"]
        )
    )
    test = pl.read_parquet(runs_root / run_id / "test_predictions.parquet")  # inner folds only
    frame = test.join(market, on="match_key", how="left")
    splits = load_split_config(config_dir / "splits.yaml")
    columns = ["p_elo_overall_recal", "p_rank_recal", "p_proportional_a", "p_power_a", "p_shin_a"]
    result = compare_on_identical_population(frame, columns, holdout_start=splits.holdout_start)
    typer.echo(json.dumps(result, indent=2, default=str))


Protocol = Annotated[Path, typer.Option(help="Pre-registered protocol YAML.")]
DEFAULT_PROTOCOL = Path("configs/preregistration/market_benchmark_v1.yaml")


@odds_app.command("plan")
def plan_cmd(
    protocol_path: Protocol = DEFAULT_PROTOCOL,
    probe_max_credits: int = 40,
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
) -> None:
    """Dry-run acquisition plan: zero network calls, zero credits."""
    protocol, sha = load_protocol(protocol_path)
    tables = load_tables(resolve_build_dir(data_root / "processed"))
    view = DataView(tables, load_split_config(config_dir / "splits.yaml"))
    matches, tournaments = view.table("matches"), view.table("tournaments")
    api = _api_config(config_dir, None)
    plan = build_plan(
        matches,
        tournaments,
        build_eligibility(matches, tournaments),
        protocol,
        probe_max_credits=probe_max_credits,
        markets=api.markets,
        regions=api.regions,
    )
    typer.echo(
        json.dumps(
            {"protocol_id": protocol.protocol_id, "protocol_sha256": sha, **plan},
            indent=2,
            default=str,
        )
    )


@odds_app.command("pilot-compare")
def pilot_compare_cmd(
    protocol_path: Protocol = DEFAULT_PROTOCOL,
    stream: Annotated[str, typer.Option(help="Odds stream with linked events.")] = "historical",
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
    runs_root: Annotated[Path, typer.Option(help="Experiment registry root.")] = Path("runs"),
) -> None:
    """Run the pre-registered pilot comparison (needs acquired, linked historical odds)."""
    protocol, sha = load_protocol(protocol_path)
    out = data_root / "processed" / "odds" / PROVIDER / stream
    needed = [
        out / f for f in ("odds_events.parquet", "odds_quotes.parquet", "event_links.parquet")
    ]
    if not all(p.exists() for p in needed):
        typer.echo("no linked odds available; acquisition requires owner approval", err=True)
        raise typer.Exit(code=1)
    events, quotes, links = (pl.read_parquet(p) for p in needed)
    splits = load_split_config(config_dir / "splits.yaml")
    tables = load_tables(resolve_build_dir(data_root / "processed"))
    view = DataView(tables, splits)
    matches, tournaments = view.table("matches"), view.table("tournaments")
    predictions = pl.read_parquet(
        runs_root / protocol.model_predictions.phase2_run_id / "test_predictions.parquet"
    )
    names = {k[1]: v[0] for k, v in load_tournament_aliases().items() if k[0] == PROVIDER}
    population = pilot_population(
        matches, tournaments, build_eligibility(matches, tournaments), predictions, protocol, names
    )
    report = run_pilot(
        protocol=protocol,
        protocol_sha256=sha,
        population=population,
        events=events,
        quotes=quotes,
        links=links,
        predictions=predictions,
        holdout_start=splits.holdout_start,
    )
    typer.echo(json.dumps(report, indent=2, default=str))


def _stage_dir(data_root: Path, name: str) -> Path:
    return data_root / "processed" / "odds" / PROVIDER / "stages" / name  # gitignored


@odds_app.command("stage-plan")
def stage_plan_cmd(
    name: Annotated[str, typer.Option(help="Stage name, e.g. stage-a-wimbledon-2022.")],
    discovery_capture_id: Annotated[
        str, typer.Option(help="Stored historical capture whose schedule defines the stage.")
    ],
    max_gap_minutes: Annotated[
        float, typer.Option(help="Max decision - snapshot gap when decisions share one call.")
    ] = 15.0,
    stage_ceiling: Annotated[int, typer.Option(help="Cumulative stage credit ceiling.")] = 130,
    lead_minutes: float = 60.0,
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
) -> None:
    """Offline request manifest for one stage (zero network calls, zero credits)."""
    store = _store(data_root, "historical")
    found = [(r, c) for r, c in store.iter_captures() if r.capture_id == discovery_capture_id]
    if len(found) != 1:
        typer.echo(f"discovery capture not found: {discovery_capture_id}", err=True)
        raise typer.Exit(code=1)
    record, capture = found[0]
    sport = record.competition_key or ""
    snapshot = TheOddsApiNormalizer().normalize(capture)
    api = _api_config(config_dir, None)
    try:
        manifest = plan_stage(
            name=name,
            sport_key=sport,
            discovery_capture_id=record.capture_id,
            discovery_snapshot=snapshot.observed_at_utc,
            events=snapshot.events,
            already_captured=sorted(returned_snapshots(store, sport)),
            lead_minutes=lead_minutes,
            max_gap_minutes=max_gap_minutes,
            markets=api.markets,
            regions=api.regions,
            stage_ceiling=stage_ceiling,
        )
    except StagePlanError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    out = _stage_dir(data_root, name)
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(manifest.to_json(), encoding="utf-8", newline="\n")
    typer.echo(
        json.dumps(
            {
                "manifest": str(out / "manifest.json"),
                "discovery_snapshot_utc": manifest.discovery_snapshot_utc,
                "grid_offset_minutes": manifest.grid_offset_minutes,
                "events": sum(len(r.events) for r in manifest.requests),
                "requests": len(manifest.requests),
                "planned_credits": manifest.planned_credits,
                "stage_ceiling": manifest.stage_ceiling,
                "rows": manifest_summary(manifest),
            },
            indent=2,
        )
    )


@odds_app.command("stage-run")
def stage_run_cmd(
    name: Annotated[str, typer.Option(help="Stage name used with stage-plan.")],
    execute: Annotated[bool, typer.Option(help="Make the paid calls (default: dry run).")] = False,
    approve_paid_calls: Annotated[
        bool, typer.Option(help="Owner approval for every paid call in the manifest.")
    ] = False,
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
) -> None:
    """Walk a stage manifest with a cumulative credit ledger; dry run unless both flags are set.

    V2 day-anchor manifests run only behind the programme-wide guard (``odds.v2_guard``); paid
    execution of any other manifest is refused (v1 acquisition is suspended, D-34).
    """
    out = _stage_dir(data_root, name)
    manifest = StageManifest.from_json((out / "manifest.json").read_text(encoding="utf-8"))
    store = _store(data_root, "historical")
    repo_root = config_dir.resolve().parent
    v2 = is_v2_manifest(manifest)
    if execute and not approve_paid_calls:
        typer.echo("refusing: --execute needs --approve-paid-calls", err=True)
        raise typer.Exit(code=2)
    if execute and not v2:
        typer.echo("refusing: paid v1 stage acquisition is suspended (D-34)", err=True)
        raise typer.Exit(code=2)
    programme = None
    try:
        if v2:
            programme = v2_preflight(out, repo_root=repo_root, store=store)
        client = None
        if execute:
            config = _api_config(config_dir, manifest.planned_credits).model_copy(
                update={"max_retries": 0}
            )
            client = TheOddsApiClient(config)
        if v2:
            _, report = guarded_stage_run(
                out, repo_root=repo_root, store=store, client=client, execute=execute
            )
        else:
            report = run_stage(manifest, store=store, ledger_path=out / "ledger.jsonl")
    except V2GuardError as exc:
        typer.echo(f"REFUSED (V2 guard): {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except (StageRunStoppedError, OddsApiError) as exc:
        typer.echo(f"STOPPED: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    rows = [{k: v for k, v in r.items() if k != "capture_id"} for r in report]
    typer.echo(json.dumps({"execute": execute, "programme": programme, "rows": rows}, indent=2))


@odds_app.command("stage-audit")
def stage_audit_cmd(
    name: Annotated[str, typer.Option(help="Stage name used with stage-plan.")],
    data_root: DataRoot = Path("data"),
) -> None:
    """Outcome-blind go/no-go criteria for a completed stage (offline; run build-tables first)."""
    stage = _stage_dir(data_root, name)
    manifest = StageManifest.from_json((stage / "manifest.json").read_text(encoding="utf-8"))
    out = data_root / "processed" / "odds" / PROVIDER / "historical"
    links_path = out / "event_links.parquet"
    result = stage_audit(
        manifest,
        events=pl.read_parquet(out / "odds_events.parquet"),
        quotes=pl.read_parquet(out / "odds_quotes.parquet"),
        quarantine=pl.read_parquet(out / "odds_quote_quarantine.parquet"),
        ledger=read_ledger(stage / "ledger.jsonl"),
        links=pl.read_parquet(links_path) if links_path.exists() else None,
    )
    typer.echo(json.dumps(result, indent=2))


# ------------------------------------------------------------------- DRAFT v2 (not frozen)


def _parse_days(spec: str) -> list[date]:
    """``YYYY-MM-DD..YYYY-MM-DD`` (inclusive) or a comma-separated list."""
    if ".." in spec:
        lo, hi = (date.fromisoformat(x) for x in spec.split(".."))
        return [lo + timedelta(days=i) for i in range((hi - lo).days + 1)]
    return [date.fromisoformat(x) for x in spec.split(",") if x]


@odds_app.command("v2-manifest")
def v2_manifest_cmd(
    edition: Annotated[str, typer.Option(help="Edition id from the draft v2 calendar.")],
    name: Annotated[str, typer.Option(help="Stage name for the manifest directory.")],
    days: Annotated[str, typer.Option(help="Calendar days, e.g. 2021-08-30..2021-09-05.")],
    data_root: DataRoot = Path("data"),
) -> None:
    """DRAFT v2: write a fixed day-anchor manifest (offline; no network, no credits)."""
    calendar = load_calendar()[edition]
    try:
        manifest = day_anchor_manifest(name=name, calendar=calendar, days=_parse_days(days))
    except (StagePlanError, V2IntegrationError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    out = _stage_dir(data_root, name)
    path = out / "manifest.json"
    if path.exists():
        typer.echo(f"refusing to overwrite {path}", err=True)
        raise typer.Exit(code=1)
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.to_json(), encoding="utf-8", newline="\n")
    typer.echo(
        json.dumps(
            {
                "manifest": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "edition": edition,
                "calendar_verified": calendar.verified,
                "requests": [r.request_date_utc for r in manifest.requests],
                "planned_credits": manifest.planned_credits,
            },
            indent=2,
        )
    )


@odds_app.command("v2-lock")
def v2_lock_cmd(
    name: Annotated[str, typer.Option(help="Stage name to lock (write-once).")],
    data_root: DataRoot = Path("data"),
) -> None:
    """DRAFT v2: lock a stage's acquisition; afterwards stage-run refuses any request for it."""
    try:
        lock = write_lock(_stage_dir(data_root, name), _store(data_root, "historical"))
    except V2IntegrationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(lock, indent=2))


def _v2_report(edition: str, stages: str, config_dir: Path, data_root: Path) -> dict[str, Any]:
    calendar = load_calendar()[edition]
    manifests = []
    for name in [s for s in stages.split(",") if s]:
        stage = _stage_dir(data_root, name)
        manifests.append(
            (
                StageManifest.from_json((stage / "manifest.json").read_text(encoding="utf-8")),
                read_ledger(stage / "ledger.jsonl"),
                (stage / LOCK_FILE).exists(),
            )
        )
    out = data_root / "processed" / "odds" / PROVIDER / "historical"
    events = pl.read_parquet(out / "odds_events.parquet")
    tables = load_tables(resolve_build_dir(data_root / "processed"))
    matches, _ = identity_view(tables, load_split_config(config_dir / "splits.yaml"))
    identity_columns = [
        "match_key", "tournament_id", "draw_category", "match_day_lo", "match_day_hi",
        "prediction_cutoff_utc",
    ]  # fmt: skip
    return v2_audit(
        calendar=calendar,
        manifests=manifests,
        listings=schedule_listings(_store(data_root, "historical")),
        events=events,
        quotes=pl.read_parquet(out / "odds_quotes.parquet"),
        identity=identity_links(pl.read_parquet(out / "event_links.parquet"), events),
        canonical_matches=matches.select(identity_columns),
    )


@odds_app.command("v2-audit")
def v2_audit_cmd(
    edition: Annotated[str, typer.Option(help="Edition id from the draft v2 calendar.")],
    stages: Annotated[str, typer.Option(help="Comma-separated stage names of this edition.")],
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
) -> None:
    """DRAFT v2: outcome-blind coverage audit of the requested days (offline)."""
    report = _v2_report(edition, stages, config_dir, data_root)
    typer.echo(json.dumps(report, indent=2, default=str))


@odds_app.command("v2-tranche-plan")
def v2_tranche_plan_cmd(
    name: Annotated[str, typer.Option(help="Stage with an existing day-anchor manifest.")],
    sizes: Annotated[str, typer.Option(help="Requests per tranche, in order, e.g. 2,5.")],
    max_credits: Annotated[str, typer.Option(help="Additional credits per tranche, e.g. 20,50.")],
    data_root: DataRoot = Path("data"),
) -> None:
    """DRAFT v2: split a manifest into separately approved tranches (offline, write-once)."""
    try:
        tranches = write_tranche_plan(
            _stage_dir(data_root, name),
            sizes=[int(x) for x in sizes.split(",")],
            max_credits=[int(x) for x in max_credits.split(",")],
        )
    except V2IntegrationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps([t.__dict__ for t in tranches], indent=2))


@odds_app.command("v2-tranche-run")
def v2_tranche_run_cmd(
    name: Annotated[str, typer.Option(help="Stage with a tranche plan.")],
    tranche: Annotated[int, typer.Option(help="Tranche number to run.")],
    execute: Annotated[bool, typer.Option(help="Make the paid calls (default: dry run).")] = False,
    approve_paid_calls: Annotated[
        bool, typer.Option(help="Owner approval for the paid calls of THIS tranche only.")
    ] = False,
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
) -> None:
    """DRAFT v2: run one tranche; later tranches need a passing offline gate of the previous one."""
    stage = _stage_dir(data_root, name)
    store = _store(data_root, "historical")
    repo_root = config_dir.resolve().parent
    if execute and not approve_paid_calls:
        typer.echo("refusing: --execute needs --approve-paid-calls", err=True)
        raise typer.Exit(code=2)
    try:
        plan = {t.number: t for t in read_tranche_plan(stage)}
        if tranche not in plan:
            raise V2IntegrationError(f"no tranche {tranche}")
        programme = v2_preflight(
            stage,
            repo_root=repo_root,
            store=store,
            request_ids=frozenset(plan[tranche].request_ids),
        )
        client = None
        if execute:
            config = _api_config(config_dir, plan[tranche].max_credits).model_copy(
                update={"max_retries": 0}
            )
            client = TheOddsApiClient(config)
        _, report = guarded_tranche_run(
            stage, tranche, repo_root=repo_root, store=store, client=client, execute=execute
        )
    except V2GuardError as exc:
        typer.echo(f"REFUSED (V2 guard): {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except (StageRunStoppedError, OddsApiError, V2IntegrationError) as exc:
        typer.echo(f"STOPPED: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    out = {"execute": execute, "tranche": tranche, "programme": programme, "rows": report}
    typer.echo(json.dumps(out, indent=2))


@odds_app.command("v2-tranche-check")
def v2_tranche_check_cmd(
    edition: Annotated[str, typer.Option(help="Edition id from the draft v2 calendar.")],
    name: Annotated[str, typer.Option(help="Stage with a tranche plan.")],
    tranche: Annotated[int, typer.Option(help="Tranche number to gate.")],
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
) -> None:
    """DRAFT v2: offline gate for one tranche (run build-tables and link first; write-once)."""
    report = _v2_report(edition, name, config_dir, data_root)
    try:
        gate = check_tranche(
            _stage_dir(data_root, name),
            tranche,
            calendar=load_calendar()[edition],
            audit_report=report,
        )
    except V2IntegrationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(gate, indent=2, default=str))
    if gate["status"] != "pass":
        raise typer.Exit(code=1)
