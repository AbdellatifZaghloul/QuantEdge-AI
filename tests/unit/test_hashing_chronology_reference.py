"""Hashing, AT-15 day instants, reference invariants, AT-28 splits and AT-20 licence lineage."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from quantedge.canonical.reference import load_reference
from quantedge.core.chronology import (
    earliest_instant,
    earliest_instant_expr,
    latest_instant,
    latest_instant_expr,
)
from quantedge.core.hashing import frame_content_hash, length_prefixed
from quantedge.core.lineage import (
    BuildProfile,
    LicenceClass,
    LicenceError,
    assert_exportable,
    assert_profile_allows,
)
from quantedge.core.splits import load_split_config, walk_forward_folds
from tests.conftest import REPO_ROOT

# ----------------------------------------------------------------------------- hashing


def test_length_prefix_is_unambiguous() -> None:
    assert length_prefixed(["ab", "c"]) != length_prefixed(["a", "bc"])


def test_content_hash_ignores_row_and_column_order() -> None:
    frame = pl.DataFrame({"a": [1, 2, 3], "b": ["x", None, "z"]})
    shuffled = frame.reverse().select("b", "a")
    assert frame_content_hash(frame) == frame_content_hash(shuffled)


def test_content_hash_distinguishes_null_from_text() -> None:
    a = pl.DataFrame({"s": [None]}, schema={"s": pl.String})
    b = pl.DataFrame({"s": ["NULL"]})
    c = pl.DataFrame({"s": [""]})
    assert len({frame_content_hash(a), frame_content_hash(b), frame_content_hash(c)}) == 3


def test_content_hash_excludes_locators() -> None:
    a = pl.DataFrame({"k": [1], "row": [5]})
    b = pl.DataFrame({"k": [1], "row": [9]})
    assert frame_content_hash(a, exclude=["row"]) == frame_content_hash(b, exclude=["row"])
    assert frame_content_hash(a) != frame_content_hash(b)


# ----------------------------------------------------------------------------- chronology


def test_day_instants_explicit_values() -> None:
    day = date(2023, 1, 2)
    assert earliest_instant(day) == datetime(2023, 1, 1, 10, 0, tzinfo=UTC)
    assert latest_instant(day) == datetime(2023, 1, 3, 12, 0, tzinfo=UTC)
    # A calendar day spans 50 hours of UTC instants (24h + 14h ahead + 12h behind).
    assert (latest_instant(day) - earliest_instant(day)).total_seconds() == 50 * 3600


def test_day_instant_expressions_match_python() -> None:
    days = [date(2020, 2, 28), date(2020, 2, 29), date(2024, 12, 31)]
    frame = pl.DataFrame({"d": days}).with_columns(
        earliest_instant_expr("d").alias("e"), latest_instant_expr("d").alias("l")
    )
    for d, e, l_ in frame.iter_rows():
        assert e == earliest_instant(d) and l_ == latest_instant(d)


# ----------------------------------------------------------------------------- reference


def test_reference_invariants() -> None:
    ref = load_reference()
    ordinals = {c: r.ordinal for c, r in ref.rounds.items()}
    assert ordinals["Q1"] < ordinals["Q3"] < ordinals["R128"] < ordinals["R16"] < ordinals["QF"]
    assert ordinals["QF"] < ordinals["SF"] < ordinals["BR"] < ordinals["F"]
    assert not ref.rounds["ER"].precedence_eligible
    ends = {rule.event_class: rule.end_offset_days for rule in ref.class_rules}
    for (event_class, _), (lo, hi) in ref.day_bounds.items():
        assert lo <= hi <= ends[event_class]


def test_event_class_rules() -> None:
    ref = load_reference()
    assert ref.event_class("G", 128).event_class == "slam"
    assert ref.event_class("M", 96).event_class == "two_week"
    assert ref.event_class("M", 56).event_class == "one_week"
    assert ref.event_class("A", None).event_class == "other"  # missing draw -> widest bounds
    assert ref.event_class("Z", 32).event_class == "other"
    assert ref.bounds("davis_cup", "RR") == ref.bounds("davis_cup", "*")


# ----------------------------------------------------------------------------- splits (AT-28)


def test_splits_are_chronological_and_inside_development() -> None:
    config = load_split_config(REPO_ROOT / "configs" / "splits.yaml")
    folds = walk_forward_folds(config)
    assert [f.test_start.year for f in folds] == list(range(2013, 2025))
    for fold in folds:
        assert fold.fit_start <= fold.fit_end < fold.calibration_start <= fold.calibration_end
        assert fold.calibration_end < fold.test_start <= fold.test_end
        assert fold.test_end <= config.development_end < config.holdout_start
        assert fold.fit_start >= config.development_start
    assert config.holdout_start == date(2025, 1, 1)


def test_split_config_rejects_fold_in_holdout(tmp_path: Path) -> None:
    text = (REPO_ROOT / "configs" / "splits.yaml").read_text(encoding="utf-8")
    bad = tmp_path / "splits.yaml"
    bad.write_text(
        text.replace("late_check_years: [2023, 2024]", "late_check_years: [2023, 2025]"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="development period"):
        load_split_config(bad)


# ----------------------------------------------------------------------------- lineage (AT-20)


def test_commercial_profile_blocks_research_lineage() -> None:
    assert_profile_allows([LicenceClass.RESEARCH_NC], BuildProfile.RESEARCH)
    with pytest.raises(LicenceError):
        assert_profile_allows([LicenceClass.RESEARCH_NC], BuildProfile.COMMERCIAL)
    assert_profile_allows([LicenceClass.COMMERCIAL_LICENSED], BuildProfile.COMMERCIAL)
    with pytest.raises(LicenceError):
        assert_profile_allows([], BuildProfile.RESEARCH)
    with pytest.raises(LicenceError):
        assert_exportable([LicenceClass.RESEARCH_PRIVATE])
