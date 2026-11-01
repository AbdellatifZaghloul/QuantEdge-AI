"""The Odds API adapter (fake transport only) and the append-only capture store."""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quantedge.core.http import HttpResponse
from quantedge.core.lineage import LicenceClass
from quantedge.odds.store import CaptureStore, StoreIntegrityError
from quantedge.odds.tables import build_odds_tables
from quantedge.odds.theoddsapi import (
    BudgetExceededError,
    MissingCredentialError,
    OddsApiConfig,
    OddsApiError,
    PaidCallNotApprovedError,
    TheOddsApiClient,
    TheOddsApiNormalizer,
    cost_historical_odds,
    cost_live_odds,
)
from tests.fixtures.odds_synthetic import ScriptedTransport, event, historical_body, live_body, ok

KEY = "synthetic-key-0123456789abcdef"
T0 = datetime(2024, 7, 1, 9, 0, tzinfo=UTC)


def client(transport: ScriptedTransport, budget: int = 100, **kw: object) -> TheOddsApiClient:
    config = OddsApiConfig(max_credits_per_run=budget, regions=("eu",), markets=("h2h",))
    return TheOddsApiClient(
        config, api_key=KEY, transport=transport, sleep=lambda s: None, clock=lambda: T0, **kw
    )  # type: ignore[arg-type]


def sample_events() -> list[dict[str, object]]:
    return [
        event(
            "ev1",
            "Alpha Synth",
            "Beta Synth",
            T0 + timedelta(hours=3),
            [
                ("bookone", T0 - timedelta(minutes=2), 1.5, 2.6),
                ("booktwo", T0 - timedelta(minutes=5), 1.55, 2.45),
            ],
            totals=True,
        )
    ]


def test_costs() -> None:
    assert cost_live_odds(["h2h"], ["eu"]) == 1
    assert cost_live_odds(["h2h", "totals"], ["eu", "uk"]) == 4
    assert cost_historical_odds(["h2h"], ["eu"]) == 10
    assert cost_historical_odds(["h2h", "spreads"], ["eu", "uk", "us"]) == 60


def test_missing_key_and_default_zero_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QE_ODDS_API_KEY", raising=False)
    transport = ScriptedTransport([])
    with pytest.raises(MissingCredentialError):
        TheOddsApiClient(OddsApiConfig(), transport=transport).sports()
    with pytest.raises(BudgetExceededError):  # default config allows no chargeable request
        TheOddsApiClient(OddsApiConfig(), api_key=KEY, transport=transport).live_odds("x")
    assert transport.urls == []  # nothing was sent


def test_historical_requires_approval_and_budget() -> None:
    transport = ScriptedTransport([])
    with pytest.raises(PaidCallNotApprovedError):
        client(transport).historical_odds("tennis_atp_wimbledon", T0, approved=False)
    with pytest.raises(BudgetExceededError):
        client(transport, budget=9).historical_odds("tennis_atp_wimbledon", T0, approved=True)
    assert transport.urls == []


def test_budget_accumulates_across_calls() -> None:
    transport = ScriptedTransport([ok(live_body([]), last=1), ok(live_body([]), last=1)])
    c = client(transport, budget=2)
    c.live_odds("s")
    c.live_odds("s")
    with pytest.raises(BudgetExceededError):
        c.live_odds("s")
    assert c.credits_spent == 2


def test_retry_with_backoff_then_success_and_key_redaction() -> None:
    sleeps: list[float] = []
    transport = ScriptedTransport(
        [HttpResponse(429, {"Retry-After": "3"}), HttpResponse(503, {}), ok(live_body([]))]
    )
    c = client(transport)
    c._sleep = sleeps.append  # type: ignore[method-assign]
    capture = c.live_odds("tennis_atp_wimbledon")
    assert sleeps[0] == 3.0 and len(sleeps) == 2
    assert all(KEY not in repr(v) for v in (capture, capture.request))
    assert ("regions", "eu") in capture.request.params and capture.credits_remaining == 499


def test_non_retryable_errors_raise_without_leaking_key() -> None:
    transport = ScriptedTransport([HttpResponse(401, {}, f"bad key {KEY}".encode())])
    with pytest.raises(OddsApiError) as info:
        client(transport).live_odds("s")
    assert KEY not in str(info.value) and "***" in str(info.value)
    exhausted = ScriptedTransport([HttpResponse(429, {})] * 5)
    with pytest.raises(OddsApiError, match="429"):
        client(exhausted).live_odds("s")
    assert len(exhausted.urls) == 5  # 1 + max_retries(4)


def test_normalizer_live_and_historical() -> None:
    transport = ScriptedTransport(
        [
            ok(live_body(sample_events())),
            ok(historical_body(T0 - timedelta(minutes=1), sample_events()), last=10),
        ]
    )
    c = client(transport)
    live = TheOddsApiNormalizer().normalize(c.live_odds("tennis_atp_wimbledon"))
    hist = TheOddsApiNormalizer().normalize(
        c.historical_odds("tennis_atp_wimbledon", T0, approved=True)
    )
    assert live.capture_mode == "live" and live.observed_at_utc == T0
    assert hist.capture_mode == "historical" and hist.observed_at_utc == T0 - timedelta(minutes=1)
    markets = {(q.market, q.selection_role) for q in live.quotes}
    assert ("match_winner", "participant") in markets and ("total_games", "over") in markets
    assert {q.line for q in live.quotes if q.market == "total_games"} == {22.5}
    assert live.events[0].participant_1 == "Alpha Synth"


# ----------------------------------------------------------------------------- store


def _capture(body: bytes, at: datetime = T0):  # type: ignore[no-untyped-def]
    transport = ScriptedTransport([ok(body)])
    c = TheOddsApiClient(
        OddsApiConfig(max_credits_per_run=10), api_key=KEY, transport=transport, clock=lambda: at
    )
    return c.live_odds("tennis_atp_wimbledon")


def test_store_is_append_only_and_idempotent(tmp_path: Path) -> None:
    store = CaptureStore(tmp_path, "the_odds_api", LicenceClass.COMMERCIAL_LICENSED, "prospective")
    capture = _capture(live_body(sample_events()))
    first, created = store.append(capture)
    again, created_again = store.append(capture)
    assert created and not created_again and first == again and len(store.records()) == 1
    later, created_later = store.append(
        _capture(live_body(sample_events()), T0 + timedelta(minutes=5))
    )
    assert created_later and later.sha256 == first.sha256 and len(store.records()) == 2
    bodies = list((store.root / "bodies").rglob("*.json"))
    assert len(bodies) == 1  # identical content stored once
    assert not os.access(bodies[0], os.W_OK) or not (bodies[0].stat().st_mode & stat.S_IWRITE)
    assert "prospective" in str(store.root) and "commercial" in str(store.root)
    assert KEY not in store.log_path.read_text(encoding="utf-8")


def test_store_detects_tampering(tmp_path: Path) -> None:
    store = CaptureStore(tmp_path, "the_odds_api", LicenceClass.COMMERCIAL_LICENSED, "prospective")
    store.append(_capture(live_body(sample_events())))
    body = next((store.root / "bodies").rglob("*.json"))
    body.chmod(stat.S_IWRITE | stat.S_IREAD)
    body.write_bytes(body.read_bytes().replace(b"1.5", b"9.9"))
    with pytest.raises(StoreIntegrityError):
        list(store.iter_captures())


def test_tables_are_deterministic_and_carry_provenance(tmp_path: Path) -> None:
    store = CaptureStore(tmp_path, "the_odds_api", LicenceClass.COMMERCIAL_LICENSED, "prospective")
    store.append(_capture(live_body(sample_events())))
    events, quotes = build_odds_tables(store, TheOddsApiNormalizer())  # type: ignore[arg-type]
    again_events, again_quotes = build_odds_tables(store, TheOddsApiNormalizer())  # type: ignore[arg-type]
    assert events.equals(again_events) and quotes.equals(again_quotes)
    assert events.height == 1 and quotes.height == 8  # 2 books x (2 match-winner + 2 totals)
    assert set(quotes["price_semantics"]) == {"observed_quote_not_guaranteed_executable"}
    assert set(quotes["licence_class"]) == {"COMMERCIAL_LICENSED"}
    assert set(quotes["stream"]) == {"prospective"} and set(quotes["timing_class"]) == {
        "timestamped"
    }
