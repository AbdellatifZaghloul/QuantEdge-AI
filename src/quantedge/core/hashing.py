"""Deterministic hashing primitives.

Everything here is independent of platform, process and Python's randomised ``hash()``:
inputs are explicit UTF-8 bytes and outputs are hex digests.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from pathlib import Path

import polars as pl

_CHUNK = 1 << 20
# Contains NUL bytes, which never occur in source CSV text, so it cannot collide with a value.
_NULL_SENTINEL = "\x00NULL\x00"


def length_prefixed(fields: Iterable[str]) -> bytes:
    """Encode fields as ``<len>:<utf8 bytes>`` so field boundaries are unambiguous."""
    out = bytearray()
    for field in fields:
        raw = field.encode("utf-8")
        out += str(len(raw)).encode("ascii") + b":" + raw
    return bytes(out)


def sha256_fields(domain: str, *fields: str) -> str:
    """SHA-256 hex digest of a domain-separated, length-prefixed field sequence."""
    return hashlib.sha256(length_prefixed((domain, *fields))).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_sha1(path: Path) -> str:
    """Git object id of a file's contents (``sha1("blob <size>\\0" + content)``)."""
    size = path.stat().st_size
    digest = hashlib.sha1(f"blob {size}\0".encode("ascii"), usedforsecurity=False)
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def row_hashes(frame: pl.DataFrame, columns: Sequence[str], domain: str) -> pl.Series:
    """Per-row SHA-256 over the given columns (nulls encoded distinctly from empty strings)."""
    values = frame.select(columns).rows()
    hashes = [
        sha256_fields(domain, *("\x00NULL" if v is None else str(v) for v in row)) for row in values
    ]
    return pl.Series("source_row_sha256", hashes, dtype=pl.String)


def frame_content_hash(frame: pl.DataFrame, *, exclude: Sequence[str] = ()) -> str:
    """Canonical content hash of a table.

    Columns are taken in name order (excluding ``exclude``), rows are sorted by every column,
    and values are serialised with a fixed CSV encoding. The digest also covers the schema.
    Row order of the input therefore never affects the result.
    """
    columns = sorted(c for c in frame.columns if c not in set(exclude))
    canonical = frame.select(columns)
    if columns:
        canonical = canonical.sort(columns, nulls_last=True, maintain_order=True)
    schema = ";".join(f"{name}:{dtype}" for name, dtype in canonical.schema.items())
    body = canonical.write_csv(
        include_header=True,
        line_terminator="\n",
        null_value="\\N",
        datetime_format="%Y-%m-%dT%H:%M:%S%.6f",
        date_format="%Y-%m-%d",
        quote_style="always",
    )
    digest = hashlib.sha256()
    digest.update(schema.encode("utf-8"))
    digest.update(b"\n")
    digest.update(body.encode("utf-8"))
    return digest.hexdigest()
