"""Append-only experiment registry under ``runs/`` (gitignored).

Each run directory holds ``manifest.json`` (parameters, data/code hashes, seeds, splits) and
``metrics.json``; ``runs/registry.jsonl`` gets one line per run. Late-check looks are counted in
``runs/late_check_looks.json`` and capped (VALIDATION.md §2.2).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from quantedge.core.hashing import sha256_fields

REGISTRY = "registry.jsonl"
LOOKS = "late_check_looks.json"


class LateCheckLimitError(PermissionError):
    """Raised when a model version has used all permitted late-check looks."""


def git_state(repo_root: Path) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}
    return {"commit": commit, "dirty": dirty}


def make_run_id(payload: dict[str, Any]) -> str:
    return sha256_fields("qe.run.v1", json.dumps(payload, sort_keys=True, default=str))[:12]


def environment() -> dict[str, str]:
    import numpy as np  # noqa: PLC0415 - version capture only

    return {
        "python": sys.version.split()[0],
        "polars": pl.__version__,
        "numpy": np.__version__,
    }


def register(
    runs_root: Path, run_dir: Path, manifest: dict[str, Any], metrics: dict[str, Any]
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = manifest | {"created_at_utc": stamp}
    for name, payload in (("manifest.json", manifest), ("metrics.json", metrics)):
        (run_dir / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    line = {
        "run_id": manifest["run_id"],
        "name": manifest["name"],
        "kind": manifest["kind"],
        "created_at_utc": stamp,
        "git": manifest["git"],
        "build_id": manifest["data"]["build_id"],
        "headline": metrics.get("headline", {}),
    }
    with (runs_root / REGISTRY).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(line, sort_keys=True, default=str) + "\n")


def read_registry(runs_root: Path) -> list[dict[str, Any]]:
    path = runs_root / REGISTRY
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def record_late_check_look(runs_root: Path, version: str, max_looks: int, note: str) -> int:
    path = runs_root / LOOKS
    looks: dict[str, list[dict[str, str]]] = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    )
    used = looks.get(version, [])
    if len(used) >= max_looks:
        raise LateCheckLimitError(f"{version}: all {max_looks} late-check looks used")
    used.append({"at_utc": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "note": note})
    looks[version] = used
    runs_root.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(looks, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return len(used)
