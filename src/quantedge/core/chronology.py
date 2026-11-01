"""Conservative time conversion for data without timezones (ARCHITECTURE.md §5.2-5.4).

- *Earliest instant* of a calendar day D anywhere on Earth: ``D 00:00`` at UTC+14.
- *Latest instant* of D: ``D 24:00`` at UTC-12.

A match played on an unknown day within ``[day_lo, day_hi]`` cannot have started before
``earliest_instant(day_lo)`` and is certainly finished (absent suspensions beyond the bounds) by
``latest_instant(day_hi)``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import yaml
from pydantic import BaseModel, ConfigDict

UTC_DATETIME = pl.Datetime("us", "UTC")


class ChronologyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    earliest_utc_offset_hours: int = 14
    latest_utc_offset_hours: int = -12
    ranking_knowability: str = "latest_instant_of_ranking_date"


def load_chronology_config(path: Path) -> ChronologyConfig:
    return ChronologyConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _midnight_utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def earliest_instant(day: date, config: ChronologyConfig | None = None) -> datetime:
    offset = (config or ChronologyConfig()).earliest_utc_offset_hours
    return _midnight_utc(day) - timedelta(hours=offset)


def latest_instant(day: date, config: ChronologyConfig | None = None) -> datetime:
    offset = (config or ChronologyConfig()).latest_utc_offset_hours
    return _midnight_utc(day) + timedelta(days=1) - timedelta(hours=offset)


def earliest_instant_expr(column: str, config: ChronologyConfig | None = None) -> pl.Expr:
    offset = (config or ChronologyConfig()).earliest_utc_offset_hours
    return pl.col(column).cast(UTC_DATETIME) - pl.duration(hours=offset)


def latest_instant_expr(column: str, config: ChronologyConfig | None = None) -> pl.Expr:
    offset = (config or ChronologyConfig()).latest_utc_offset_hours
    return pl.col(column).cast(UTC_DATETIME) + pl.duration(days=1) - pl.duration(hours=offset)
