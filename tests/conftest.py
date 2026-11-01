"""Shared fixtures. Network access is blocked for the whole test session (AT-04)."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from quantedge.canonical.build import BuildResult, build
from quantedge.core.manifest import RawManifest
from tests.fixtures.synthetic import materialise

REPO_ROOT = Path(__file__).resolve().parents[1]


class NetworkBlockedError(RuntimeError):
    """Raised when a test attempts a non-loopback network connection."""


def _is_loopback(address: Any) -> bool:
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, bytes):
        host = host.decode()
    if not isinstance(host, str):
        return False
    if host in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex


def _guarded_connect(self: socket.socket, address: Any) -> Any:
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address):
        raise NetworkBlockedError(f"network access blocked in tests: {address!r}")
    return _original_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> Any:
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address):
        raise NetworkBlockedError(f"network access blocked in tests: {address!r}")
    return _original_connect_ex(self, address)


def pytest_configure(config: pytest.Config) -> None:
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]


@pytest.fixture(scope="session")
def synthetic_snapshot(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, RawManifest]:
    root = tmp_path_factory.mktemp("synthetic_data")
    snapshot, manifest, _ = materialise(root)
    return snapshot, manifest


@pytest.fixture(scope="session")
def synthetic_build(
    synthetic_snapshot: tuple[Path, RawManifest], tmp_path_factory: pytest.TempPathFactory
) -> BuildResult:
    snapshot, manifest = synthetic_snapshot
    return build(snapshot, manifest, tmp_path_factory.mktemp("processed"))


@pytest.fixture
def matches(synthetic_build: BuildResult) -> Iterator[pl.DataFrame]:
    yield synthetic_build.tables["matches"]
