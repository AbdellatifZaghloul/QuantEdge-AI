"""AT-03 fetch provenance (synthetic remote) and AT-04 network blocking."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from quantedge.adapters.research.sackmann_archive import (
    FetchError,
    TamperedFileError,
    fetch,
    verify_snapshot,
)
from quantedge.core.hashing import sha256_file
from tests.conftest import NetworkBlockedError
from tests.fixtures.synthetic import FakeRemote, materialise, synthetic_files


def test_fetch_selects_pinned_files_and_records_hashes(tmp_path: Path) -> None:
    snapshot, manifest, remote = materialise(tmp_path)
    paths = [f.path for f in manifest.files]
    assert "atp/atp_matches_doubles_2023.csv" not in paths  # not selected by include patterns
    assert remote.downloads == len(paths) == 6
    for entry in manifest.files:
        assert sha256_file(snapshot / entry.path) == entry.sha256
        assert len(entry.git_blob_sha1) == 40
    assert len(manifest.commit) == 40


def test_second_fetch_downloads_nothing_and_works_offline(tmp_path: Path) -> None:
    _, first, _ = materialise(tmp_path)
    remote = FakeRemote(synthetic_files())
    second = fetch(remote.config, tmp_path, http_get=remote)
    assert remote.requests == []  # manifest present: no tree call, no downloads
    assert second.downloaded == 0 and second.verified_existing == len(first.files)
    assert [f.sha256 for f in second.manifest.files] == [f.sha256 for f in first.files]


def test_tampered_file_fails_fetch_and_verify_naming_the_file(tmp_path: Path) -> None:
    snapshot, _, _ = materialise(tmp_path)
    target = snapshot / "atp" / "atp_players.csv"
    target.write_bytes(target.read_bytes() + b"999,Tampered,Row,R,,,,\n")
    remote = FakeRemote(synthetic_files())
    with pytest.raises(TamperedFileError, match=r"atp/atp_players\.csv"):
        fetch(remote.config, tmp_path, http_get=remote)
    with pytest.raises(TamperedFileError, match=r"atp/atp_players\.csv"):
        verify_snapshot(snapshot)


def test_download_not_matching_pinned_blob_is_rejected(tmp_path: Path) -> None:
    files = synthetic_files()
    remote = FakeRemote(files)
    corrupted = dict(files)

    def serve(url: str) -> bytes:
        if url.endswith("atp_players.csv"):
            return corrupted["atp/atp_players.csv"] + b"extra"
        return remote(url)

    with pytest.raises(FetchError, match="does not match pinned git blob"):
        fetch(remote.config, tmp_path, http_get=serve)
    assert list((tmp_path / "raw").rglob("*.part")) == []  # partial download removed


def test_network_is_blocked() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkBlockedError):
            sock.connect(("93.184.216.34", 80))
        with pytest.raises(NetworkBlockedError):
            socket.create_connection(("93.184.216.34", 443), timeout=1)
    finally:
        sock.close()
