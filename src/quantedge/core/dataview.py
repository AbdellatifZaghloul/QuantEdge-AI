"""Development/holdout separation enforced in code (ARCHITECTURE.md §5.8, D-05).

Phase 1 builds include 2025+ rows so they can be schema-validated. Every consumer that develops,
selects or tunes models must read through ``DataView(mode="development")``, which never returns
rows on or after ``holdout_start`` and raises if a request reaches it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import polars as pl

from quantedge.core.splits import SplitConfig

Mode = Literal["development", "holdout"]

# Column carrying the partitioning date for each canonical table.
_DATE_COLUMN = {
    "matches": "tournament_start_date",
    "rankings": "ranking_date",
    "tournaments": "start_date",
}
# Tables keyed by match: filtered through the development match keys.
_MATCH_CHILDREN = ("match_entry", "match_stats")


class HoldoutAccessError(PermissionError):
    """Raised when development code requests sealed holdout data."""


class DataView:
    def __init__(
        self,
        tables: dict[str, pl.DataFrame],
        splits: SplitConfig,
        *,
        mode: Mode = "development",
        reason: str | None = None,
        access_log: Path | None = None,
    ) -> None:
        if mode == "holdout":
            if not reason:
                raise HoldoutAccessError("holdout access requires an explicit reason")
            if access_log is None:
                raise HoldoutAccessError("holdout access requires an access log path")
            access_log.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            with access_log.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{stamp}\tholdout\t{reason}\n")
        self._tables = tables
        self._splits = splits
        self.mode: Mode = mode

    @property
    def last_date(self) -> date | None:
        """Last date this view may expose (None = unrestricted, holdout mode)."""
        return self._splits.development_end if self.mode == "development" else None

    def _check(self, end: date | None) -> date | None:
        if self.mode == "holdout":
            return end
        if end is not None and end >= self._splits.holdout_start:
            raise HoldoutAccessError(
                f"development view cannot read up to {end}; holdout starts "
                f"{self._splits.holdout_start}"
            )
        return end if end is not None else self._splits.development_end

    def table(self, name: str, *, end: date | None = None) -> pl.DataFrame:
        limit = self._check(end)
        frame = self._tables[name]
        if name in _DATE_COLUMN:
            return frame if limit is None else frame.filter(pl.col(_DATE_COLUMN[name]) <= limit)
        if name in _MATCH_CHILDREN:
            keys = self.table("matches", end=end).select("match_key")
            return frame.join(keys, on="match_key", how="semi")
        if name == "players":
            return frame
        if self.mode == "development":
            raise HoldoutAccessError(f"table {name!r} is not available in development views")
        return frame
