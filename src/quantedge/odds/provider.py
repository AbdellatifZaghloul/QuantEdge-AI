"""Provider-independent interfaces for odds sources.

A provider adapter has two responsibilities, kept separate so the pipeline never depends on one
vendor's wire format:

1. **Fetch** raw responses (network, credentials, retries, budgets) -> ``RawCapture``.
2. **Normalise** a stored raw capture into canonical ``NormalizedEvent`` / ``NormalizedQuote``
   records (pure function of the stored bytes plus capture metadata).

Everything downstream (store, linking, selection, de-vig) consumes the normalised form only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from quantedge.core.lineage import LicenceClass


@dataclass(frozen=True)
class CaptureRequest:
    """A redacted description of what was requested (never contains credentials)."""

    endpoint: str
    competition_key: str | None
    params: tuple[tuple[str, str], ...]
    snapshot_requested_utc: str | None = None


@dataclass(frozen=True)
class RawCapture:
    provider: str
    request: CaptureRequest
    captured_at_utc: datetime
    status: int
    body: bytes
    credits_last: int | None = None
    credits_used: int | None = None
    credits_remaining: int | None = None
    regions: tuple[str, ...] = ()


@dataclass(frozen=True)
class NormalizedEvent:
    provider_event_id: str
    competition_key: str
    competition_title: str | None
    participant_1: str
    participant_2: str
    commence_time_utc: datetime


@dataclass(frozen=True)
class NormalizedQuote:
    provider_event_id: str
    bookmaker_key: str
    bookmaker_title: str | None
    region: str | None
    market: str
    provider_market_key: str
    selection_name: str
    selection_role: str
    line: float | None
    decimal_odds: float  # NaN when the provider value is missing or not a number
    commence_time_utc: datetime
    market_last_update_utc: datetime | None
    price_raw: str | None = None  # the provider's value exactly as received (JSON-encoded)
    price_issue: str | None = None  # quarantine reason for a structurally invalid price, else None


@dataclass(frozen=True)
class NormalizedSnapshot:
    provider: str
    observed_at_utc: datetime
    captured_at_utc: datetime
    capture_mode: str  # "live" | "historical"
    events: Sequence[NormalizedEvent] = field(default_factory=tuple)
    quotes: Sequence[NormalizedQuote] = field(default_factory=tuple)


class OddsProvider(Protocol):
    """Interface every odds provider adapter implements."""

    name: str
    licence_class: LicenceClass

    def normalize(self, capture: RawCapture) -> NormalizedSnapshot:
        """Pure transformation of a stored raw capture into canonical records."""
        ...
