"""Date-filtered lazy Parquet reader: rows after ``end`` are never materialized.

Equivalent to ``DataView(load_tables(build_dir), splits, mode="development").table(name, end=end)``
for the dated tables and the match-child tables, but the date predicate (and, for match children,
the semi-join on permitted match keys) is applied inside the lazy scan, before collection, so
holdout rows never enter memory as a DataFrame.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path

import polars as pl

from quantedge.core.dataview import HoldoutAccessError
from quantedge.core.splits import SplitConfig

DATE_COLUMNS = {
    "matches": "tournament_start_date",
    "rankings": "ranking_date",
    "tournaments": "start_date",
}
MATCH_CHILDREN = ("match_entry", "match_stats")


def scan_development(build_dir: Path, name: str, end: date) -> pl.LazyFrame:
    if name in DATE_COLUMNS:
        return pl.scan_parquet(build_dir / f"{name}.parquet").filter(
            pl.col(DATE_COLUMNS[name]) <= end
        )
    if name in MATCH_CHILDREN:
        keys = scan_development(build_dir, "matches", end).select("match_key")
        return pl.scan_parquet(build_dir / f"{name}.parquet").join(keys, on="match_key", how="semi")
    raise HoldoutAccessError(f"table {name!r} has no development date filter")


def load_development_tables(
    build_dir: Path, splits: SplitConfig, names: Iterable[str], *, end: date | None = None
) -> dict[str, pl.DataFrame]:
    limit = splits.development_end if end is None else end
    if limit >= splits.holdout_start:
        raise HoldoutAccessError(
            f"cannot read up to {limit}; holdout starts {splits.holdout_start}"
        )
    out = {name: scan_development(build_dir, name, limit).collect() for name in names}
    for name, column in DATE_COLUMNS.items():
        if name in out:
            latest = out[name][column].max()
            if latest is not None and latest > limit:  # type: ignore[operator]
                raise HoldoutAccessError(f"{name}: rows after {limit} were materialized")
    return out
