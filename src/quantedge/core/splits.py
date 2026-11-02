"""Evidence partitions and chronological walk-forward folds (VALIDATION.md §2, D-05)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator


class SplitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    burn_in_end: date
    development_start: date
    development_end: date
    holdout_start: date
    inner_test_years: tuple[int, ...]
    late_check_years: tuple[int, ...]
    late_check_max_looks: int

    @model_validator(mode="after")
    def _ordered(self) -> SplitConfig:
        if not (self.burn_in_end < self.development_start <= self.development_end):
            raise ValueError("burn-in must end before development, which must be non-empty")
        if self.holdout_start <= self.development_end:
            raise ValueError("holdout must start after development ends")
        years = (*self.inner_test_years, *self.late_check_years)
        if list(years) != sorted(set(years)):
            raise ValueError("test years must be strictly increasing and unique")
        if years and date(max(years), 12, 31) > self.development_end:
            raise ValueError("every fold must end within the development period")
        return self

    @property
    def inner_end(self) -> date:
        """Last day of the inner walk-forward folds (the late check and holdout lie after it)."""
        return (
            date(max(self.inner_test_years), 12, 31) if self.inner_test_years else self.burn_in_end
        )


@dataclass(frozen=True)
class Fold:
    name: str
    kind: str  # "inner" or "late_check"
    fit_start: date
    fit_end: date
    calibration_start: date
    calibration_end: date
    test_start: date
    test_end: date


def load_split_config(path: Path) -> SplitConfig:
    return SplitConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _fold(config: SplitConfig, year: int, kind: str) -> Fold:
    return Fold(
        name=f"{kind}_{year}",
        kind=kind,
        fit_start=config.development_start,
        fit_end=date(year - 2, 12, 31),
        calibration_start=date(year - 1, 1, 1),
        calibration_end=date(year - 1, 12, 31),
        test_start=date(year, 1, 1),
        test_end=date(year, 12, 31),
    )


def walk_forward_folds(config: SplitConfig) -> list[Fold]:
    folds = [_fold(config, y, "inner") for y in config.inner_test_years]
    folds += [_fold(config, y, "late_check") for y in config.late_check_years]
    for fold in folds:
        if fold.fit_end < fold.fit_start:
            raise ValueError(f"{fold.name}: empty fit window")
    return folds
