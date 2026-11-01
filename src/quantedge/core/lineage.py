"""Licence classes and build-profile enforcement (ARCHITECTURE.md §7, D-02)."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class LicenceClass(StrEnum):
    RESEARCH_NC = "RESEARCH_NC"
    RESEARCH_PRIVATE = "RESEARCH_PRIVATE"
    COMMERCIAL_LICENSED = "COMMERCIAL_LICENSED"
    OWN_COLLECTED = "OWN_COLLECTED"


class BuildProfile(StrEnum):
    RESEARCH = "research"
    COMMERCIAL = "commercial"


class LicenceError(PermissionError):
    """Raised when an artifact's licence lineage is not permitted for the requested use."""


_RESEARCH_ONLY = frozenset({LicenceClass.RESEARCH_NC, LicenceClass.RESEARCH_PRIVATE})


def assert_profile_allows(lineage: Iterable[LicenceClass | str], profile: BuildProfile) -> None:
    classes = {LicenceClass(c) for c in lineage}
    if not classes:
        raise LicenceError("artifact has no recorded licence lineage")
    if profile is BuildProfile.COMMERCIAL:
        blocked = sorted(classes & _RESEARCH_ONLY)
        if blocked:
            raise LicenceError(
                f"commercial profile cannot load artifacts derived from {', '.join(blocked)}"
            )


def assert_exportable(lineage: Iterable[LicenceClass | str]) -> None:
    classes = {LicenceClass(c) for c in lineage}
    if LicenceClass.RESEARCH_PRIVATE in classes:
        raise LicenceError("RESEARCH_PRIVATE-derived artifacts may not be exported or served")
