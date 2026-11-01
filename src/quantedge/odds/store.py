"""Append-only, content-addressed store for raw provider captures.

Layout (under the gitignored data root)::

    data/raw/<licence_dir>/<provider>/<stream>/
        bodies/<sha[:2]>/<sha256>.json   # raw response body, written once, then read-only
        captures.jsonl                   # one line per observation (append-only)

- Bodies are content-addressed and created with exclusive-create semantics, so an existing body is
  never overwritten. Identical content observed again is recorded as a new observation line that
  points at the same body.
- Ingestion is idempotent: recording the same observation (same body hash, capture time and
  request) twice adds nothing.
- Reads verify the SHA-256 of every body; any alteration raises ``StoreIntegrityError``.
- The prospective stream (forward capture) and the historical stream are stored separately.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from quantedge.core.lineage import LicenceClass
from quantedge.odds.provider import CaptureRequest, RawCapture

Stream = Literal["prospective", "historical"]
_LICENCE_DIR = {
    LicenceClass.COMMERCIAL_LICENSED: "commercial",
    LicenceClass.RESEARCH_NC: "research_nc",
    LicenceClass.RESEARCH_PRIVATE: "research_private",
    LicenceClass.OWN_COLLECTED: "own",
}


class StoreIntegrityError(RuntimeError):
    """Raised when a stored body no longer matches its recorded hash."""


@dataclass(frozen=True)
class CaptureRecord:
    capture_id: str
    provider: str
    stream: str
    endpoint: str
    competition_key: str | None
    params: list[list[str]]
    snapshot_requested_utc: str | None
    captured_at_utc: str
    status: int
    sha256: str
    bytes: int
    credits_last: int | None
    credits_used: int | None
    credits_remaining: int | None
    regions: list[str]


class CaptureStore:
    def __init__(self, data_root: Path, provider: str, licence_class: LicenceClass, stream: Stream):
        self.root = data_root / "raw" / _LICENCE_DIR[licence_class] / provider / stream
        self.provider = provider
        self.stream = stream
        self.log_path = self.root / "captures.jsonl"

    def _body_path(self, sha: str) -> Path:
        return self.root / "bodies" / sha[:2] / f"{sha}.json"

    def append(self, capture: RawCapture) -> tuple[CaptureRecord, bool]:
        """Store a capture; returns ``(record, created)``. Never rewrites existing data."""
        if capture.provider != self.provider:
            raise ValueError("capture provider does not match store")
        sha = hashlib.sha256(capture.body).hexdigest()
        body_path = self._body_path(sha)
        if body_path.exists():
            self._verify(body_path, sha)
        else:
            body_path.parent.mkdir(parents=True, exist_ok=True)
            with body_path.open("xb") as handle:  # exclusive create: never overwrite
                handle.write(capture.body)
            body_path.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
        captured = _iso(capture.captured_at_utc)
        identity = json.dumps(
            [
                sha,
                captured,
                capture.request.endpoint,
                list(capture.request.params),
                capture.request.snapshot_requested_utc,
            ],
            sort_keys=True,
        )
        capture_id = "cap1_" + hashlib.sha256(identity.encode()).hexdigest()[:32]
        if any(r.capture_id == capture_id for r in self.records()):
            existing = next(r for r in self.records() if r.capture_id == capture_id)
            return existing, False
        record = CaptureRecord(
            capture_id=capture_id,
            provider=capture.provider,
            stream=self.stream,
            endpoint=capture.request.endpoint,
            competition_key=capture.request.competition_key,
            params=[list(p) for p in capture.request.params],
            snapshot_requested_utc=capture.request.snapshot_requested_utc,
            captured_at_utc=captured,
            status=capture.status,
            sha256=sha,
            bytes=len(capture.body),
            credits_last=capture.credits_last,
            credits_used=capture.credits_used,
            credits_remaining=capture.credits_remaining,
            regions=list(capture.regions),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record, True

    def records(self) -> list[CaptureRecord]:
        if not self.log_path.exists():
            return []
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        return [CaptureRecord(**json.loads(line)) for line in lines if line.strip()]

    def _verify(self, path: Path, sha: str) -> bytes:
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != sha:
            raise StoreIntegrityError(f"{path.name}: content does not match its SHA-256")
        return body

    def iter_captures(self) -> Iterator[tuple[CaptureRecord, RawCapture]]:
        """Yield verified captures in log order."""
        for record in self.records():
            body = self._verify(self._body_path(record.sha256), record.sha256)
            yield (
                record,
                RawCapture(
                    provider=record.provider,
                    request=CaptureRequest(
                        endpoint=record.endpoint,
                        competition_key=record.competition_key,
                        params=tuple(tuple(p) for p in record.params),  # type: ignore[misc]
                        snapshot_requested_utc=record.snapshot_requested_utc,
                    ),
                    captured_at_utc=datetime.fromisoformat(record.captured_at_utc),
                    status=record.status,
                    body=body,
                    credits_last=record.credits_last,
                    credits_used=record.credits_used,
                    credits_remaining=record.credits_remaining,
                    regions=tuple(record.regions),
                ),
            )


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("capture timestamps must be timezone-aware UTC")
    return value.isoformat()
