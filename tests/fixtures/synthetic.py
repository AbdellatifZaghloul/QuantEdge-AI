"""Hand-designed synthetic snapshot in the Sackmann file layout.

All players, tournaments and results are invented. Nothing here is derived from licensed data. The
dataset deliberately contains the edge cases the pipeline must handle: duplicates (within and across
files), key collisions with and without distinct ``match_num``, invalid rows, padded/zero-prefixed
IDs, retirements, walkovers, unknown scores, holdout-season rows, and ranking conflicts.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import polars as pl

from quantedge.adapters.research.sackmann_archive import SourceConfig, fetch
from quantedge.canonical.build import (
    FILE_PRIORITY,
    RAW_MATCH_COLUMNS,
    RAW_PLAYER_COLUMNS,
    RAW_RANKING_COLUMNS,
)
from quantedge.core.hashing import row_hashes
from quantedge.core.manifest import RawManifest

COMMIT = "0" * 39 + "1"


def synthetic_config() -> SourceConfig:
    return SourceConfig(
        source="synthetic_archive",
        repo="example/synthetic-archive",
        commit=COMMIT,
        licence="CC BY-NC-SA 4.0 (synthetic test data)",
        licence_class="RESEARCH_NC",  # type: ignore[arg-type]
        attribution="Synthetic test data",
        include_patterns=(
            r"^atp/atp_matches_[0-9]{4}\.csv$",
            r"^atp/atp_matches_qual_chall_[0-9]{4}\.csv$",
            r"^atp/atp_players\.csv$",
            r"^atp/atp_rankings_[0-9a-z]+\.csv$",
        ),
        raw_url_template="https://example.invalid/{repo}/{commit}/{path}",
        tree_url_template="https://example.invalid/{repo}/tree/{commit}",
    )


def match_row(**kw: Any) -> dict[str, str]:
    """A raw match row with neutral defaults; keyword args override columns."""
    row = {c: "" for c in RAW_MATCH_COLUMNS}
    row.update(
        tourney_id="2023-0001", tourney_name="Synthetic Open", surface="Hard", draw_size="8",
        tourney_level="A", tourney_date="20230102", match_num="1", best_of="3", round="QF",
        score="6-4 6-4", minutes="90",
    )  # fmt: skip
    for key, value in kw.items():
        row[key] = "" if value is None else str(value)
    for side in ("winner", "loser"):
        pid = row[f"{side}_id"].strip().lstrip("0") or "0"
        row.setdefault(f"{side}_name", "")
        if not row[f"{side}_name"]:
            row[f"{side}_name"] = f"Synthetic {pid}"
    return row


def stats(prefix: str, svpt: int, first_in: int, first_won: int, second_won: int,
          aces: int = 3, df: int = 2, sv_gms: int = 10, bp_saved: int = 2,
          bp_faced: int = 4) -> dict[str, str]:  # fmt: skip
    return {
        f"{prefix}_ace": str(aces), f"{prefix}_df": str(df), f"{prefix}_svpt": str(svpt),
        f"{prefix}_1stIn": str(first_in), f"{prefix}_1stWon": str(first_won),
        f"{prefix}_2ndWon": str(second_won), f"{prefix}_SvGms": str(sv_gms),
        f"{prefix}_bpSaved": str(bp_saved), f"{prefix}_bpFaced": str(bp_faced),
    }  # fmt: skip


def tour_2023() -> list[dict[str, str]]:
    base = {"tourney_id": "2023-0001", "tourney_date": "20230102", "draw_size": "8"}
    rows = [
        match_row(**base, round="QF", match_num=1, winner_id=101, loser_id=102,
                  winner_rank=1, loser_rank=40, **stats("w", 60, 40, 30, 10),
                  **stats("l", 55, 30, 20, 12)),
        match_row(**base, round="QF", match_num=2, winner_id=103, loser_id=104,
                  score="7-6(5) 3-6 6-2", winner_rank=12, loser_rank=9),
        match_row(**base, round="QF", match_num=3, winner_id=105, loser_id=106,
                  score="6-3 2-1 RET", winner_rank=5, loser_rank=6),
        match_row(**base, round="QF", match_num=4, winner_id=107, loser_id=108, score="W/O"),
        match_row(**base, round="SF", match_num=5, winner_id=101, loser_id=103,
                  score="6-4 4-6 [10-8]"),
        # Zero-prefixed and padded IDs must canonicalise to sackmann:105 / sackmann:107.
        match_row(**base, round="SF", match_num=6, winner_id="0105", loser_id=" 107 ",
                  score="6-2 6-2"),
        match_row(**base, round="F", match_num=7, winner_id=105, loser_id=101, score="UNK"),
        # Exact duplicate of the first QF row (same content): kept once.
        None,
        # Grand-slam style best-of-five event.
        match_row(tourney_id="2023-0002", tourney_name="Synthetic Slam", tourney_level="G",
                  draw_size="4", tourney_date="20230116", best_of=5, round="SF", match_num=1,
                  winner_id=111, loser_id=112, score="6-4 7-6(3) 6-7(5) 6-3"),
        match_row(tourney_id="2023-0002", tourney_name="Synthetic Slam", tourney_level="G",
                  draw_size="4", tourney_date="20230116", best_of=5, round="SF", match_num=2,
                  winner_id=113, loser_id=114, score="6-1 6-1 6-1"),
        match_row(tourney_id="2023-0002", tourney_name="Synthetic Slam", tourney_level="G",
                  draw_size="4", tourney_date="20230116", best_of=5, round="F", match_num=3,
                  winner_id=113, loser_id=111, score="3-6 6-3 6-4 6-4", minutes=""),
        # Round-robin event: a resolvable collision (distinct match_num) and an unresolvable one.
        match_row(tourney_id="2023-0004", tourney_name="Synthetic Finals", tourney_level="F",
                  draw_size="4", tourney_date="20231113", round="RR", match_num=1,
                  winner_id=115, loser_id=116, score="6-4 6-4"),
        match_row(tourney_id="2023-0004", tourney_name="Synthetic Finals", tourney_level="F",
                  draw_size="4", tourney_date="20231113", round="RR", match_num=2,
                  winner_id=116, loser_id=115, score="7-5 7-5"),
        match_row(tourney_id="2023-0004", tourney_name="Synthetic Finals", tourney_level="F",
                  draw_size="4", tourney_date="20231113", round="RR", match_num=3,
                  winner_id=117, loser_id=118, score="6-1 6-1"),
        match_row(tourney_id="2023-0004", tourney_name="Synthetic Finals", tourney_level="F",
                  draw_size="4", tourney_date="20231113", round="RR", match_num=3,
                  winner_id=118, loser_id=117, score="6-2 6-2"),
        # Invalid rows, each with a distinct rejection reason.
        match_row(round="QF", match_num=90, winner_id="12a", loser_id=101),
        match_row(round="QF", match_num=91, winner_id=101, loser_id=101),
        match_row(round="XX", match_num=92, winner_id=101, loser_id=104),
        match_row(tourney_date="2023-01-02", round="QF", match_num=93, winner_id=101, loser_id=106),
        match_row(tourney_id="", round="QF", match_num=94, winner_id=101, loser_id=108),
        # Invalid statistic value: becomes null, counted in build notes.
        match_row(tourney_id="2023-0005", tourney_name="Synthetic Indoor", tourney_level="A",
                  draw_size="2", tourney_date="20230213", round="F", match_num=1,
                  winner_id=119, loser_id=120, w_ace="a", **stats("l", 50, 30, 20, 10)),
    ]  # fmt: skip
    rows[7] = dict(rows[0])
    return [r for r in rows if r is not None]


def qual_chall_2023() -> list[dict[str, str]]:
    base = {"tourney_id": "2023-0001", "tourney_date": "20230102", "draw_size": "8"}
    rows = [
        match_row(**base, round="Q1", match_num=201, winner_id=109, loser_id=110),
        match_row(**base, round="Q2", match_num=202, winner_id=109, loser_id=111),
        match_row(tourney_id="2023-1001", tourney_name="Synthetic Challenger", tourney_level="C",
                  draw_size="32", tourney_date="20230102", round="R32", match_num=1,
                  winner_id=112, loser_id=114, score="6-3 6-7(4) 6-4"),
    ]  # fmt: skip
    return rows


def tour_2025() -> list[dict[str, str]]:
    return [
        match_row(tourney_id="2025-0001", tourney_name="Synthetic Holdout Open", draw_size="2",
                  tourney_date="20250106", round="F", match_num=1, winner_id=101, loser_id=105),
        match_row(tourney_id="2025-0002", tourney_name="Synthetic Year End", draw_size="2",
                  tourney_date="20241230", round="F", match_num=1, winner_id=102, loser_id=103),
    ]  # fmt: skip


def players() -> list[dict[str, str]]:
    rows = [
        {"player_id": str(pid), "name_first": "Synthetic", "name_last": f"P{pid}",
         "hand": "R" if pid % 3 else "L", "dob": f"199{pid % 10}0{pid % 9 + 1}15", "ioc": "XXX",
         "height": str(170 + pid % 25), "wikidata_id": ""}
        for pid in range(101, 121)
    ]  # fmt: skip
    rows.append(dict(rows[0]))  # exact duplicate -> DUPLICATE_ROW
    rows.append({"player_id": "x9", "name_first": "Bad", "name_last": "Id", "hand": "R",
                 "dob": "", "ioc": "", "height": "", "wikidata_id": ""})  # fmt: skip
    conflict = {"player_id": "130", "name_first": "Twin", "name_last": "A", "hand": "U",
                "dob": "19800000", "ioc": "", "height": "999", "wikidata_id": ""}  # fmt: skip
    rows += [conflict, conflict | {"name_last": "B"}]
    rows.append({"player_id": "131", "name_first": "Year", "name_last": "Only", "hand": "",
                 "dob": "19800000", "ioc": "", "height": "999", "wikidata_id": ""})  # fmt: skip
    return rows


def rankings_20s() -> list[dict[str, str]]:
    rows = []
    for date in ("20221226", "20230102", "20230109"):
        for i, pid in enumerate(range(101, 109), start=1):
            rows.append({"ranking_date": date, "rank": str(i), "player": str(pid),
                         "points": str(5000 - 100 * i)})  # fmt: skip
    rows.append(dict(rows[0]))  # exact duplicate
    rows.append({"ranking_date": "20230109", "rank": "99", "player": "101", "points": "1"})
    rows.append({"ranking_date": "2023-01-16", "rank": "1", "player": "101", "points": ""})
    rows.append({"ranking_date": "20230116", "rank": "0", "player": "101", "points": ""})
    return rows


def rankings_current() -> list[dict[str, str]]:
    return [{"ranking_date": "20250106", "rank": "1", "player": "101", "points": ""}]


def _csv(rows: Iterable[dict[str, str]], columns: tuple[str, ...]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    return buffer.getvalue().encode("utf-8")


def synthetic_files() -> dict[str, bytes]:
    tour = tour_2023()
    qual = qual_chall_2023()
    tour.append(dict(qual[0]))  # cross-file duplicate of the first qualifying row
    return {
        "atp/atp_matches_2023.csv": _csv(tour, RAW_MATCH_COLUMNS),
        "atp/atp_matches_2025.csv": _csv(tour_2025(), RAW_MATCH_COLUMNS),
        "atp/atp_matches_qual_chall_2023.csv": _csv(qual, RAW_MATCH_COLUMNS),
        "atp/atp_players.csv": _csv(players(), RAW_PLAYER_COLUMNS),
        "atp/atp_rankings_20s.csv": _csv(rankings_20s(), RAW_RANKING_COLUMNS),
        "atp/atp_rankings_current.csv": _csv(rankings_current(), RAW_RANKING_COLUMNS),
        "atp/atp_matches_doubles_2023.csv": b"not,selected\n",
    }


def git_blob_sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


class FakeRemote:
    """In-memory stand-in for the GitHub tree API and raw file host."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.requests: list[str] = []
        self.config = synthetic_config()

    def __call__(self, url: str) -> bytes:
        self.requests.append(url)
        if url == self.config.tree_url():
            tree = [
                {"path": p, "type": "blob", "sha": git_blob_sha1_bytes(b), "size": len(b)}
                for p, b in sorted(self.files.items())
            ]
            return json.dumps({"truncated": False, "tree": tree}).encode()
        for path, body in self.files.items():
            if url == self.config.raw_url(path):
                return body
        raise AssertionError(f"unexpected URL {url}")

    @property
    def downloads(self) -> int:
        return sum(1 for u in self.requests if u != self.config.tree_url())


def materialise(
    data_root: Path, files: dict[str, bytes] | None = None
) -> tuple[Path, RawManifest, FakeRemote]:
    """Fetch the synthetic snapshot through the real fetch code path with a fake remote."""
    remote = FakeRemote(files if files is not None else synthetic_files())
    result = fetch(remote.config, data_root, http_get=remote)
    return result.snapshot_dir, result.manifest, remote


def raw_match_frame(rows: list[dict[str, str]], *, file: str = "atp/atp_matches_2023.csv",
                    season: int = 2023, category: str = "tour") -> pl.DataFrame:  # fmt: skip
    """In-memory equivalent of the build's raw match reader (for direct ``build_matches`` tests)."""
    frame = pl.DataFrame(
        {c: [r.get(c) or None for r in rows] for c in RAW_MATCH_COLUMNS},
        schema={c: pl.String for c in RAW_MATCH_COLUMNS},
    ).with_columns(
        pl.lit(file).alias("source_file"),
        pl.int_range(1, pl.len() + 1, dtype=pl.Int64).alias("source_row_number"),
        pl.lit(category).alias("file_category"),
        pl.lit(FILE_PRIORITY[category], dtype=pl.Int8).alias("file_priority"),
        pl.lit(season, dtype=pl.Int16).alias("season"),
    )
    return frame.with_columns(row_hashes(frame, RAW_MATCH_COLUMNS, "qe.source_row.v1"))
