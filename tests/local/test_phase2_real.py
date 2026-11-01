"""[local] Gate 0 on the real snapshot: every Phase 1 anomaly is classified; no contamination."""

from __future__ import annotations

import polars as pl
import pytest

from quantedge.canonical.build import load_tables
from quantedge.core.splits import load_split_config
from quantedge.curation.audit import build_audit
from quantedge.curation.eligibility import build_eligibility, load_curation_reference
from tests.conftest import REPO_ROOT
from tests.local.test_real_snapshot import BUILD_DIR

pytestmark = pytest.mark.local
SPLITS = load_split_config(REPO_ROOT / "configs" / "splits.yaml")


@pytest.fixture(scope="module")
def tables() -> dict[str, pl.DataFrame]:
    return load_tables(BUILD_DIR)


@pytest.fixture(scope="module")
def eligibility(tables: dict[str, pl.DataFrame]) -> pl.DataFrame:
    return build_eligibility(tables["matches"], tables["tournaments"])


def _total(rows: list[dict[str, object]]) -> int:
    return sum(int(r["len"]) for r in rows)  # type: ignore[call-overload]


def test_all_phase1_anomalies_are_classified(
    tables: dict[str, pl.DataFrame], eligibility: pl.DataFrame
) -> None:
    audit = build_audit(
        tables["matches"], tables["tournaments"], tables["players"], eligibility, SPLITS
    )
    anomalies = audit["phase1_anomalies"]
    assert _total(anomalies["inconsistent_scores_development"]) == 1468
    assert _total(anomalies["precedence_conflicts_all_years"]) == 174
    assert _total(anomalies["precedence_conflicts_development"]) == 148
    knockout = anomalies["knockout_violation_pairs_development"]
    assert _total(knockout) == 93
    assert "other" not in {r["category"] for r in knockout}
    assert audit["identities"]["rule_candidates_not_curated"] == []


def test_placeholders_and_contradictions_never_train_or_evaluate(eligibility: pl.DataFrame) -> None:
    bad = eligibility.filter(
        pl.any_horizontal("placeholder_player", "duplicate_record", "contradictory_duplicate",
                          "knockout_contradiction")
        | (pl.col("primary_reason") == "SCORE_CONTRADICTION")
    )  # fmt: skip
    assert bad.height > 0
    assert not bad["elo_update_baseline"].any() and not bad["elo_update_retirements"].any()
    assert not bad["eval_eligible"].any()
    placeholders = load_curation_reference().placeholder_ids
    assert placeholders == {"sackmann:199999", "sackmann:200816"}


def test_deferred_rows_are_never_earlier(
    tables: dict[str, pl.DataFrame], eligibility: pl.DataFrame
) -> None:
    joined = eligibility.join(
        tables["matches"].select("match_key", "knowable_at_utc"), on="match_key"
    )
    assert (
        joined.filter(pl.col("effective_knowable_at_utc") < pl.col("knowable_at_utc")).height == 0
    )
