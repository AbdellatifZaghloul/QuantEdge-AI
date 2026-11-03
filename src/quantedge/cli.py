"""Command-line interface: ``quantedge data fetch | verify | build | qa``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from quantedge.adapters.research.sackmann_archive import (
    FetchError,
    fetch,
    load_source_config,
    snapshot_dir,
    verify_snapshot,
)
from quantedge.canonical.build import build, load_tables, resolve_build_dir
from quantedge.core.chronology import load_chronology_config
from quantedge.core.splits import load_split_config
from quantedge.curation.audit import run_audit
from quantedge.experiments.phase2 import load_phase2_config, run_phase2
from quantedge.experiments.phase5 import load_phase5_config, run_phase5
from quantedge.experiments.phase5_review import run_phase5_review
from quantedge.experiments.registry import LateCheckLimitError, read_registry
from quantedge.odds import commands as odds_commands
from quantedge.quality.report import build_report, write_report

app = typer.Typer(no_args_is_help=True, add_completion=False, help="QuantEdge AI research CLI.")
data_app = typer.Typer(no_args_is_help=True, help="Research data pipeline (Phase 1).")
app.add_typer(data_app, name="data")
experiment_app = typer.Typer(no_args_is_help=True, help="Phase 2 experiments (development data).")
app.add_typer(experiment_app, name="experiment")
app.add_typer(odds_commands.odds_app, name="odds")

ConfigDir = Annotated[Path, typer.Option(help="Directory containing YAML configs.")]
DataRoot = Annotated[Path, typer.Option(help="Root of the gitignored data directory.")]


@data_app.command("fetch")
def fetch_cmd(config_dir: ConfigDir = Path("configs"), data_root: DataRoot = Path("data")) -> None:
    """Download (or verify) the pinned research snapshot."""
    config = load_source_config(config_dir / "sources.yaml")
    try:
        result = fetch(config, data_root)
    except FetchError as exc:
        typer.echo(f"fetch failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    total = sum(f.size_bytes for f in result.manifest.files)
    typer.echo(
        f"commit {config.commit}: {len(result.manifest.files)} files, {total} bytes; "
        f"downloaded {result.downloaded}, verified {result.verified_existing}"
    )


@data_app.command("verify")
def verify_cmd(config_dir: ConfigDir = Path("configs"), data_root: DataRoot = Path("data")) -> None:
    """Re-hash every file of the pinned snapshot against its manifest (offline)."""
    config = load_source_config(config_dir / "sources.yaml")
    try:
        manifest = verify_snapshot(snapshot_dir(data_root, config))
    except (FetchError, FileNotFoundError) as exc:
        typer.echo(f"verify failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"verified {len(manifest.files)} files at {manifest.commit}")


@data_app.command("build")
def build_cmd(config_dir: ConfigDir = Path("configs"), data_root: DataRoot = Path("data")) -> None:
    """Build canonical Parquet tables from the verified snapshot."""
    config = load_source_config(config_dir / "sources.yaml")
    snapshot = snapshot_dir(data_root, config)
    try:
        manifest = verify_snapshot(snapshot)
    except (FetchError, FileNotFoundError) as exc:
        typer.echo(f"snapshot not verified: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    chrono = load_chronology_config(config_dir / "chronology.yaml")
    result = build(snapshot, manifest, data_root / "processed", chrono=chrono, repo_root=Path())
    for table in result.manifest.tables:
        typer.echo(f"{table.name:14s} {table.rows:>9d} rows  sha256 {table.content_sha256}")
    typer.echo(f"build {result.build_id} -> {result.out_dir}")


@data_app.command("qa")
def qa_cmd(
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
    build_id: Annotated[str | None, typer.Option(help="Build ID (default: latest).")] = None,
) -> None:
    """Write qa_report.json and qa_report.md for a build."""
    build_dir = resolve_build_dir(data_root / "processed", build_id)
    splits = load_split_config(config_dir / "splits.yaml")
    report = build_report(load_tables(build_dir), build_dir, splits)
    json_path, md_path = write_report(report, build_dir)
    checks = report["development"]["orientation_checks"]
    typer.echo(json.dumps({"orientation_checks_passed": checks["passed"]}, indent=None))
    typer.echo(f"wrote {json_path} and {md_path}")


@data_app.command("audit")
def audit_cmd(
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
    build_id: Annotated[str | None, typer.Option(help="Build ID (default: latest).")] = None,
) -> None:
    """Gate 0: classify anomalies and write modelling eligibility for a build."""
    build_dir = resolve_build_dir(data_root / "processed", build_id)
    splits = load_split_config(config_dir / "splits.yaml")
    eligibility, audit = run_audit(build_dir, load_tables(build_dir), splits)
    dev = audit["development_through_dev_end"]
    typer.echo(
        f"eligibility rows {eligibility.height}; development: update={dev['elo_update_baseline']} "
        f"eval={dev['eval_eligible']} deferred={dev['knowability_deferred']}"
    )
    typer.echo(f"wrote {build_dir / 'derived'}")


ExperimentConfig = Annotated[Path, typer.Option(help="Experiment YAML.")]
RunsRoot = Annotated[Path, typer.Option(help="Experiment registry root (gitignored).")]


@experiment_app.command("run")
def experiment_run_cmd(
    config: ExperimentConfig = Path("configs/experiments/phase2_elo.yaml"),
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
    runs_root: RunsRoot = Path("runs"),
) -> None:
    """Nested walk-forward evaluation on the inner development folds (2013-2022)."""
    _run(config, config_dir, data_root, runs_root, "inner", "")


@experiment_app.command("late-check")
def experiment_late_check_cmd(
    note: Annotated[str, typer.Option(help="Why this look is being taken.")],
    config: ExperimentConfig = Path("configs/experiments/phase2_elo.yaml"),
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
    runs_root: RunsRoot = Path("runs"),
) -> None:
    """Score the frozen protocol on 2023-2024 (counted, capped looks; never the holdout)."""
    try:
        _run(config, config_dir, data_root, runs_root, "late_check", note)
    except LateCheckLimitError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


def _run(
    config: Path, config_dir: Path, data_root: Path, runs_root: Path, kind: str, note: str
) -> None:
    text = config.read_text(encoding="utf-8")
    run_id, run_dir, metrics = run_phase2(
        resolve_build_dir(data_root / "processed"),
        load_split_config(config_dir / "splits.yaml"),
        load_phase2_config(config),
        text,
        runs_root=runs_root,
        repo_root=Path(),
        kind=kind,  # type: ignore[arg-type]
        note=note,
    )
    typer.echo(json.dumps({"run_id": run_id, "headline_log_loss": metrics["headline"]}))
    typer.echo(f"report: {run_dir / 'report.md'}")


@experiment_app.command("phase5")
def experiment_phase5_cmd(
    config: ExperimentConfig = Path("configs/experiments/phase5_gbm.yaml"),
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
    runs_root: RunsRoot = Path("runs"),
) -> None:
    """LightGBM candidate vs frozen Elo on the inner folds (reads cut at splits.inner_end)."""
    text = config.read_text(encoding="utf-8")
    run_id, run_dir, metrics = run_phase5(
        resolve_build_dir(data_root / "processed"),
        load_split_config(config_dir / "splits.yaml"),
        load_phase5_config(config),
        text,
        runs_root=runs_root,
        repo_root=Path(),
    )
    out = {
        "run_id": run_id,
        "headline_log_loss": metrics["headline"],
        "decision": metrics["decision"],
    }
    typer.echo(json.dumps(out, default=str))
    typer.echo(f"run: {run_dir}")


@experiment_app.command("phase5-review")
def experiment_phase5_review_cmd(
    config: ExperimentConfig = Path("configs/experiments/phase5_review.yaml"),
    config_dir: ConfigDir = Path("configs"),
    data_root: DataRoot = Path("data"),
    runs_root: RunsRoot = Path("runs"),
) -> None:
    """EXPLORATORY Phase 5 review on the inner folds (no late check, no holdout)."""
    run_id, run_dir, metrics = run_phase5_review(
        resolve_build_dir(data_root / "processed"),
        load_split_config(config_dir / "splits.yaml"),
        config,
        runs_root=runs_root,
        repo_root=Path(),
    )
    typer.echo(json.dumps({"run_id": run_id, "headline_log_loss": metrics["headline"]}))
    typer.echo(f"run: {run_dir}")


@experiment_app.command("list")
def experiment_list_cmd(runs_root: RunsRoot = Path("runs")) -> None:
    """List registered runs."""
    for row in read_registry(runs_root):
        typer.echo(json.dumps(row, sort_keys=True))


if __name__ == "__main__":  # pragma: no cover
    app()
