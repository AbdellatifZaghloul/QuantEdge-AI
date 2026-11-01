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
from quantedge.quality.report import build_report, write_report

app = typer.Typer(no_args_is_help=True, add_completion=False, help="QuantEdge AI research CLI.")
data_app = typer.Typer(no_args_is_help=True, help="Research data pipeline (Phase 1).")
app.add_typer(data_app, name="data")

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


if __name__ == "__main__":  # pragma: no cover
    app()
