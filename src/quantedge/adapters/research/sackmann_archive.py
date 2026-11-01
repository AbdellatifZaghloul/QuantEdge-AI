"""Research adapter: pinned Sackmann ATP data via the tennis-sackmann-archive mirror.

Licence class RESEARCH_NC (CC BY-NC-SA 4.0). Downloads are pinned to a full commit SHA, each file is
verified against the git blob SHA-1 in the tree at that commit, and our own SHA-256 is recorded.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from quantedge.core.hashing import git_blob_sha1, sha256_file
from quantedge.core.lineage import LicenceClass
from quantedge.core.manifest import (
    RAW_MANIFEST_NAME,
    RawFileEntry,
    RawManifest,
    read_raw_manifest,
    write_json,
)

HttpGet = Callable[[str], bytes]

_USER_AGENT = "quantedge-research-fetch/0.1"
_TIMEOUT_S = 120
_CONNECT_TIMEOUT_S = 10.0
_RETRIES = 3


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    repo: str
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    licence: str
    licence_class: LicenceClass
    attribution: str
    include_patterns: tuple[str, ...]
    raw_url_template: str
    tree_url_template: str

    def raw_url(self, path: str) -> str:
        return self.raw_url_template.format(repo=self.repo, commit=self.commit, path=path)

    def tree_url(self) -> str:
        return self.tree_url_template.format(repo=self.repo, commit=self.commit)

    def selects(self, path: str) -> bool:
        return any(re.match(p, path) for p in self.include_patterns)


class FetchError(RuntimeError):
    """Raised when a download cannot be verified against the pinned commit."""


class TamperedFileError(FetchError):
    """Raised when an existing local file no longer matches its pinned hash."""


@dataclass(frozen=True)
class FetchResult:
    manifest: RawManifest
    snapshot_dir: Path
    downloaded: int
    verified_existing: int


def load_source_config(path: Path, key: str = "sackmann_archive") -> SourceConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return SourceConfig.model_validate(data[key])


def snapshot_dir(data_root: Path, config: SourceConfig) -> Path:
    return data_root / "raw" / "research_nc" / config.source / config.commit


class _IPv4FirstHTTPSConnection(http.client.HTTPSConnection):  # pragma: no cover - network
    """HTTPS connection that tries IPv4 addresses before IPv6, each with a short connect timeout.

    Python 3.12's ``socket.create_connection`` tries addresses sequentially with the full timeout,
    which stalls for minutes on hosts with broken IPv6 routing.
    """

    def connect(self) -> None:
        infos = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        infos.sort(key=lambda info: info[0] != socket.AF_INET)
        last_error: OSError | None = None
        for family, kind, proto, _, address in infos:
            sock = socket.socket(family, kind, proto)
            try:
                sock.settimeout(_CONNECT_TIMEOUT_S)
                sock.connect(address)
            except OSError as exc:
                last_error = exc
                sock.close()
                continue
            sock.settimeout(self.timeout if isinstance(self.timeout, float | int) else None)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]
            return
        raise last_error or OSError(f"cannot connect to {self.host}")


class _IPv4FirstHTTPSHandler(urllib.request.HTTPSHandler):  # pragma: no cover - network
    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_IPv4FirstHTTPSConnection, req)


def default_http_get(url: str) -> bytes:  # pragma: no cover - network
    opener = urllib.request.build_opener(_IPv4FirstHTTPSHandler())
    last_error: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310
            with opener.open(request, timeout=_TIMEOUT_S) as response:
                body: bytes = response.read()
                return body
        except OSError as exc:  # URLError subclasses OSError
            last_error = exc
            time.sleep(2**attempt)
    raise FetchError(f"failed to GET {url}: {last_error}")


def _tree_entries(config: SourceConfig, http_get: HttpGet) -> list[dict[str, Any]]:
    tree = json.loads(http_get(config.tree_url()))
    if tree.get("truncated"):
        raise FetchError("git tree listing is truncated; refusing to build an incomplete manifest")
    entries = [
        {"path": e["path"], "sha": e["sha"], "size": int(e["size"])}
        for e in tree["tree"]
        if e.get("type") == "blob" and config.selects(e["path"])
    ]
    if not entries:
        raise FetchError("no files in the pinned tree match the include patterns")
    return sorted(entries, key=lambda e: e["path"])


def _now_utc() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(
    config: SourceConfig,
    data_root: Path,
    *,
    http_get: HttpGet = default_http_get,
) -> FetchResult:
    """Download (or verify) every selected file at the pinned commit; idempotent.

    If a manifest for this commit and these patterns already exists, it is the expected file
    list and no network access is needed for verification.
    """
    target_dir = snapshot_dir(data_root, config)
    manifest_path = target_dir / RAW_MANIFEST_NAME
    previous: dict[str, RawFileEntry] = {}
    expected: list[dict[str, Any]]

    if manifest_path.exists():
        existing = read_raw_manifest(manifest_path)
        if existing.commit != config.commit:
            raise FetchError("manifest commit does not match configuration")
        previous = {f.path: f for f in existing.files}
        if existing.include_patterns == config.include_patterns:
            expected = [
                {"path": f.path, "sha": f.git_blob_sha1, "size": f.size_bytes}
                for f in existing.files
            ]
        else:
            expected = _tree_entries(config, http_get)
    else:
        expected = _tree_entries(config, http_get)

    entries: list[RawFileEntry] = []
    downloaded = verified = 0
    for item in expected:
        rel = item["path"]
        local = target_dir / rel
        if local.exists():
            if git_blob_sha1(local) != item["sha"]:
                raise TamperedFileError(
                    f"{rel}: local file does not match pinned git blob {item['sha']}"
                )
            prior = previous.get(rel)
            if prior is not None and sha256_file(local) != prior.sha256:
                raise TamperedFileError(f"{rel}: SHA-256 differs from manifest")
            verified += 1
            retrieved = prior.retrieved_at_utc if prior else _now_utc()
        else:
            body = http_get(config.raw_url(rel))
            local.parent.mkdir(parents=True, exist_ok=True)
            tmp = local.with_suffix(local.suffix + ".part")
            tmp.write_bytes(body)
            if tmp.stat().st_size != item["size"] or git_blob_sha1(tmp) != item["sha"]:
                tmp.unlink()
                raise FetchError(f"{rel}: downloaded content does not match pinned git blob")
            tmp.replace(local)
            downloaded += 1
            retrieved = _now_utc()
        entries.append(
            RawFileEntry(
                path=rel,
                url=config.raw_url(rel),
                size_bytes=local.stat().st_size,
                git_blob_sha1=item["sha"],
                sha256=sha256_file(local),
                retrieved_at_utc=retrieved,
            )
        )

    manifest = RawManifest(
        source=config.source,
        repo=config.repo,
        commit=config.commit,
        licence=config.licence,
        licence_class=config.licence_class,
        attribution=config.attribution,
        include_patterns=config.include_patterns,
        files=tuple(entries),
    )
    write_json(manifest_path, manifest)
    return FetchResult(manifest, target_dir, downloaded, verified)


def verify_snapshot(snapshot: Path) -> RawManifest:
    """Re-hash every file listed in a snapshot manifest; raise on any mismatch."""
    manifest = read_raw_manifest(snapshot / RAW_MANIFEST_NAME)
    for entry in manifest.files:
        local = snapshot / entry.path
        if not local.exists():
            raise FetchError(f"{entry.path}: missing from snapshot")
        if sha256_file(local) != entry.sha256 or git_blob_sha1(local) != entry.git_blob_sha1:
            raise TamperedFileError(f"{entry.path}: hash mismatch against manifest")
    return manifest
