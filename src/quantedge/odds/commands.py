"""``quantedge odds ...`` commands. Chargeable calls need explicit flags and a credit ceiling."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
    """One historical snapshot (10 x markets x regions credits) into the historical store."""
    client = TheOddsApiClient(_api_config(config_dir, max_credits))
    when = datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(UTC)
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
    """Walk a stage manifest with a cumulative credit ledger; dry run unless both flags are set."""
    out = _stage_dir(data_root, name)
    manifest = StageManifest.from_json((out / "manifest.json").read_text(encoding="utf-8"))
    client = None
    if execute:
        if not approve_paid_calls:
            typer.echo("refusing: --execute needs --approve-paid-calls", err=True)
            raise typer.Exit(code=2)
        config = _api_config(config_dir, manifest.planned_credits).model_copy(
            update={"max_retries": 0}
        )
        client = TheOddsApiClient(config)
    try:
        report = run_stage(
            manifest,
            store=_store(data_root, "historical"),
            ledger_path=out / "ledger.jsonl",
            client=client,
            execute=execute,
        )
    except (StageRunStoppedError, OddsApiError) as exc:
        typer.echo(f"STOPPED: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    rows = [{k: v for k, v in r.items() if k != "capture_id"} for r in report]
    typer.echo(json.dumps({"execute": execute, "rows": rows}, indent=2))


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
