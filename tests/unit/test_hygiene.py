"""AT-21: repository hygiene guard (rules + the real tracked/staged file set)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from quantedge.devtools.hygiene import path_violations, secret_violations, tracked_files
from tests.conftest import REPO_ROOT


@pytest.mark.parametrize(
    "path",
    [
        "data/raw/research_nc/x/atp/atp_matches_2023.csv",
        "runs/abc/manifest.json",
        ".env",
        ".env.local",
        "out/matches.parquet",
        "notebooks/odds.xlsx",
        "atp_matches_2023.csv",
        "docs/export.csv",
    ],
)
def test_forbidden_paths_are_flagged(path: str) -> None:
    assert path_violations([path])


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        "tests/golden/orientation_vectors.csv",
        "src/quantedge/reference/round_ordinal.csv",
        "docs/ARCHITECTURE.md",
        "src/quantedge/canonical/build.py",
    ],
)
def test_allowed_paths_pass(path: str) -> None:
    assert path_violations([path]) == []


def test_secret_patterns(tmp_path: Path) -> None:
    fake_key = "AKIA" + "Q" * 16  # assembled at runtime so no secret-like literal is committed
    (tmp_path / "leak.py").write_text(f'aws = "{fake_key}"\n', encoding="utf-8")
    (tmp_path / "env.txt").write_text("QE_ODDS_API_KEY=" + "a1" * 12 + "\n", encoding="utf-8")
    (tmp_path / "ok.txt").write_text("QE_ODDS_API_KEY=\n", encoding="utf-8")
    problems = secret_violations(["leak.py", "env.txt", "ok.txt"], tmp_path)
    assert len(problems) == 2 and not any("ok.txt" in p for p in problems)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_repository_index_is_clean() -> None:
    files = tracked_files(REPO_ROOT)
    assert path_violations(files, REPO_ROOT) == []
    assert secret_violations(files, REPO_ROOT) == []
