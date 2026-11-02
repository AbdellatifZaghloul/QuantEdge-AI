"""CLI safety: chargeable odds commands refuse without explicit approval and a credit ceiling."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from quantedge.cli import app
from tests.conftest import REPO_ROOT

runner = CliRunner()
CONFIGS = str(REPO_ROOT / "configs")


@pytest.fixture(autouse=True)
def fake_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QE_ODDS_API_KEY", "synthetic-not-a-real-key")


def test_live_capture_requires_confirmation(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "odds",
            "capture-live",
            "--sport",
            "tennis_atp_wimbledon",
            "--config-dir",
            CONFIGS,
            "--data-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2 and not (tmp_path / "raw").exists()


def test_live_capture_blocked_by_default_zero_budget(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "odds",
            "capture-live",
            "--sport",
            "tennis_atp_wimbledon",
            "--confirm-credit-use",
            "--config-dir",
            CONFIGS,
            "--data-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1 and "budget" in result.output
    assert not (tmp_path / "raw").exists()


def test_historical_requires_approval_even_with_budget(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "odds",
            "capture-historical",
            "--sport",
            "tennis_atp_wimbledon",
            "--at",
            "2024-07-01T10:00:00Z",
            "--max-credits",
            "100",
            "--config-dir",
            CONFIGS,
            "--data-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1 and "approval" in result.output
    assert "synthetic-not-a-real-key" not in result.output


def test_compare_without_linked_odds_fails_cleanly(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "odds",
            "compare",
            "--run-id",
            "none",
            "--config-dir",
            CONFIGS,
            "--data-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1 and "no linked odds" in result.output


def test_v2_manifest_is_offline_calendar_bound_and_write_once(tmp_path: Path) -> None:
    args = [
        "odds",
        "v2-manifest",
        "--edition",
        "uso-2021",
        "--name",
        "feas",
        "--data-root",
        str(tmp_path),
    ]
    made = runner.invoke(app, [*args, "--days", "2021-08-30..2021-09-05"])
    assert made.exit_code == 0, made.output
    assert '"planned_credits": 70' in made.output and '"calendar_verified": false' in made.output
    again = runner.invoke(app, [*args, "--days", "2021-08-30..2021-09-05"])
    assert again.exit_code == 1 and "refusing to overwrite" in again.output
    outside = runner.invoke(
        app,
        ["odds", "v2-manifest", "--edition", "uso-2021", "--name", "x", "--days", "2021-09-20",
         "--data-root", str(tmp_path)],
    )  # fmt: skip
    assert outside.exit_code == 1 and "outside the frozen calendar" in outside.output
    dry = runner.invoke(app, ["odds", "stage-run", "--name", "feas", "--data-root", str(tmp_path)])
    assert dry.exit_code == 0 and dry.output.count("would_request") == 7
    assert not (tmp_path / "processed/odds/the_odds_api/stages/feas/ledger.jsonl").exists()
