"""Provenance manifests for raw downloads and canonical builds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from quantedge.core.hashing import sha256_fields
from quantedge.core.lineage import LicenceClass

RAW_MANIFEST_NAME = "manifest.json"
BUILD_MANIFEST_NAME = "build_manifest.json"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RawFileEntry(_Model):
    path: str
    url: str
    size_bytes: int
    git_blob_sha1: str
    sha256: str
    retrieved_at_utc: str


class RawManifest(_Model):
    schema_version: int = 1
    source: str
    repo: str
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    licence: str
    licence_class: LicenceClass
    attribution: str
    include_patterns: tuple[str, ...]
    files: tuple[RawFileEntry, ...]

    def fingerprint(self) -> str:
        """Content identity of the snapshot: commit + per-file SHA-256 (no timestamps)."""
        parts = [self.repo, self.commit] + [f"{f.path}={f.sha256}" for f in self.files]
        return sha256_fields("qe.raw_manifest.v1", *parts)


class TableEntry(_Model):
    name: str
    file: str
    rows: int
    content_sha256: str
    content_hash_excludes: tuple[str, ...] = ()


class BuildManifest(_Model):
    schema_version: int = 1
    build_id: str
    raw_manifest_fingerprint: str
    reference_fingerprint: str
    code_version: str
    code_fingerprint: str
    git_commit: str | None
    git_dirty: bool | None
    python_version: str
    polars_version: str
    licence_lineage: tuple[LicenceClass, ...]
    tables: tuple[TableEntry, ...]
    reconciliation: tuple[dict[str, Any], ...]
    notes: dict[str, Any]


def write_json(path: Path, model: BaseModel) -> None:
    payload = json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload + "\n", encoding="utf-8", newline="\n")


def read_raw_manifest(path: Path) -> RawManifest:
    return RawManifest.model_validate_json(path.read_text(encoding="utf-8"))


def read_build_manifest(path: Path) -> BuildManifest:
    return BuildManifest.model_validate_json(path.read_text(encoding="utf-8"))
