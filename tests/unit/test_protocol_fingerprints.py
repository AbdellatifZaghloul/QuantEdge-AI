"""Frozen pre-registered protocols are immutable: fingerprints pinned here must never change."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from quantedge.odds import v2_draft
from tests.conftest import REPO_ROOT

PREREG = REPO_ROOT / "configs" / "preregistration"
V1_SHA256 = "e3d3f642d3dd85064136c8bf8d9fc984887d1beb84d68ad9960c92ec2467da21"
V2_SHA256 = "ad319ba7384cd95d5b1c2c45fd5c3c058af6af41c6e95c3926f253e990fae70c"
CALENDAR = REPO_ROOT / "src" / "quantedge" / "reference" / "v2_draft_edition_calendar.csv"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def test_v1_is_unchanged() -> None:
    assert sha(PREREG / "market_benchmark_v1.yaml") == V1_SHA256


def test_v2_is_frozen_with_its_pinned_fingerprint() -> None:
    frozen = load(PREREG / "market_benchmark_v2.yaml")
    assert sha(PREREG / "market_benchmark_v2.yaml") == V2_SHA256
    assert frozen["protocol_id"] == "market-benchmark-v2" and frozen["status"] == "FROZEN"
    assert str(frozen["frozen_on"]) == "2026-11-03"


def test_v2_calendar_and_implementation_match_the_frozen_record() -> None:
    frozen = load(PREREG / "market_benchmark_v2.yaml")
    assert frozen["population"]["calendar_sha256"] == sha(CALENDAR)
    for rel, digest in frozen["implementation"]["files_sha256"].items():
        assert sha(REPO_ROOT / rel) == digest, rel
    assert frozen["timing"]["decision_local_time"] == "10:00"
    assert (
        frozen["timing"]["snapshot_window_minutes"] * 60
        == v2_draft.SNAPSHOT_TOLERANCE.total_seconds()
    )
    assert frozen["timing"]["min_listed_lead_minutes"] * 60 == v2_draft.MIN_LEAD.total_seconds()
    acq = frozen["acquisition"]
    assert acq["programme_ceiling_credits"] == acq["base_credits"] == 1430
    assert acq["contingency_credits"] == 0 and acq["feasibility_included_in_base"] is True
    assert [t["max_credits"] for t in acq["feasibility"]["tranches"]] == [20, 50]
    assert frozen["population"]["design_exposed_editions"] == ["wim-2022", "uso-2022"]
    assert frozen["population"]["calendar_verification"]["complete_calendar_verified"] is False


def test_frozen_v2_differs_from_the_candidate_only_where_authorised() -> None:
    frozen = load(PREREG / "market_benchmark_v2.yaml")
    cand = load(REPO_ROOT / "docs" / "preregistration" / "market-benchmark-v2-CANDIDATE.yaml")
    assert cand["status"] == "CANDIDATE_NOT_FROZEN"
    for doc in (frozen, cand):
        doc.pop("status")
        doc.pop("frozen_on")
        doc["population"].pop("calendar_sha256")
        doc["acquisition"].pop("contingency_credits")
        doc["conduct"].pop("start_of_play_uncertainty", None)
        doc.pop("implementation", None)
    assert frozen == cand
