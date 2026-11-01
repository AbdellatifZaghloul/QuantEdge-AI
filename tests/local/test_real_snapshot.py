"""[local] Acceptance tests on the real pinned research snapshot (skipped when data/ is absent).

Covers AT-03, AT-05, AT-06, AT-12, AT-14, AT-19, AT-22, AT-23 and AT-27 on real data. They never
run in CI because the licensed data is not available there.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import polars as pl
import pytest

from quantedge.adapters.research.sackmann_archive import (
    load_source_config,
    snapshot_dir,
    verify_snapshot,
)
from quantedge.canonical import schemas
from quantedge.canonical.build import build, load_tables, resolve_build_dir
from quantedge.canonical.reference import load_reference
from quantedge.core.asof import admissible_results
from quantedge.core.chronology import load_chronology_config
from quantedge.core.manifest import BUILD_MANIFEST_NAME, read_build_manifest
from quantedge.core.splits import load_split_config
from quantedge.quality.report import HOLDOUT_SECTION_KEYS, build_report
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.local

DATA = REPO_ROOT / "data"
CONFIG = load_source_config(REPO_ROOT / "configs" / "sources.yaml")
SNAPSHOT = snapshot_dir(DATA, CONFIG)
PROCESSED = DATA / "processed"

if not (SNAPSHOT / "manifest.json").exists() or not (PROCESSED / "LATEST").exists():
    pytest.skip(
        "real snapshot/build not present (run quantedge data fetch/build)", allow_module_level=True
    )

BUILD_DIR = resolve_build_dir(PROCESSED)


@pytest.fixture(scope="module")
def tables() -> dict[str, pl.DataFrame]:
    return load_tables(BUILD_DIR)


def test_at03_snapshot_verifies_against_full_commit() -> None:
    manifest = verify_snapshot(SNAPSHOT)
    assert manifest.commit == "83733587353df8a41f2fd4f516147d5aa83f5a8d"
    assert len(manifest.files) == 116


def test_at05_schemas(tables: dict[str, pl.DataFrame]) -> None:
    for name, frame in tables.items():
        schemas.validate_table(name, frame)


def test_at06_reconciliation() -> None:
    manifest = read_build_manifest(BUILD_DIR / BUILD_MANIFEST_NAME)
    assert len(manifest.reconciliation) == 116
    for row in manifest.reconciliation:
        assert row["rows_in"] == row["rows_canonical"] + row["rows_rejected"], row


def test_at14_all_rounds_known(tables: dict[str, pl.DataFrame]) -> None:
    rejected = tables["rejected_rows"]
    assert rejected.filter(pl.col("reason_code") == "UNKNOWN_ROUND").height == 0
    assert set(tables["matches"]["round"]) <= set(load_reference().rounds)


def test_at19_rebuild_reproduces_content_hashes(tmp_path: Path) -> None:
    previous = read_build_manifest(BUILD_DIR / BUILD_MANIFEST_NAME)
    chrono = load_chronology_config(REPO_ROOT / "configs" / "chronology.yaml")
    again = build(SNAPSHOT, verify_snapshot(SNAPSHOT), tmp_path, chrono=chrono)
    assert again.build_id == previous.build_id
    assert {t.name: t.content_sha256 for t in again.manifest.tables} == {
        t.name: t.content_sha256 for t in previous.tables
    }


def test_at12_at22_at23_qa_report(tables: dict[str, pl.DataFrame]) -> None:
    splits = load_split_config(REPO_ROOT / "configs" / "splits.yaml")
    report = build_report(tables, BUILD_DIR, splits)
    assert report["development"]["orientation_checks"]["passed"]
    assert set(report["holdout_integrity_only"]) <= HOLDOUT_SECTION_KEYS
    assert report["snapshot"]["max_tournament_start_date"] == "2026-06-01"  # observed, recorded
    for key in ("coverage_by_year_level",):
        assert report["development"][key]
    for key in ("rejections_by_reason", "stats_consistency_violations", "overlapping_events",
                "codes", "rank_cross_check"):  # fmt: skip
        assert key in report
    json.dumps(report, default=str)


def test_at27_point_in_time_contract_sample(tables: dict[str, pl.DataFrame]) -> None:
    matches = tables["matches"].filter(pl.col("tournament_start_date") >= pl.date(2023, 1, 1))
    matches = matches.filter(pl.col("tournament_start_date") < pl.date(2025, 1, 1))
    keys = random.Random(5).sample(matches["match_key"].to_list(), 30)
    for key in keys:
        target = matches.filter(pl.col("match_key") == key).row(0, named=True)
        admitted = admissible_results(matches, key)
        late = admitted.filter(
            (pl.col("admitted_via") == "K")
            & (pl.col("knowable_at_utc") > target["prediction_cutoff_utc"])
        )
        assert late.height == 0
        p_rows = admitted.filter(pl.col("admitted_via") == "P")
        assert p_rows.filter(pl.col("tournament_id") != target["tournament_id"]).height == 0
        assert p_rows.filter(pl.col("round_ordinal") >= target["round_ordinal"]).height == 0
