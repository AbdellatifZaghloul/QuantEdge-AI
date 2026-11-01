"""Synthetic The Odds API v4 payloads (invented events, players and prices; no real data)."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from quantedge.core.http import HttpResponse


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def outcome_market(
    key: str, last_update: datetime, prices: Sequence[tuple[str, float]], point: float | None = None
) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    for name, price in prices:
        o: dict[str, Any] = {"name": name, "price": price}
        if point is not None:
            o["point"] = point
        outcomes.append(o)
    return {"key": key, "last_update": iso(last_update), "outcomes": outcomes}


def event(
    event_id: str,
    p1: str,
    p2: str,
    commence: datetime,
    books: Sequence[tuple[str, datetime, float, float]],
    *,
    sport_key: str = "tennis_atp_wimbledon",
    totals: bool = False,
) -> dict[str, Any]:
    bookmakers = []
    for key, last, o1, o2 in books:
        markets = [outcome_market("h2h", last, [(p1, o1), (p2, o2)])]
        if totals:
            markets.append(outcome_market("totals", last, [("Over", 1.9), ("Under", 1.9)], 22.5))
        bookmakers.append(
            {"key": key, "title": key.title(), "last_update": iso(last), "markets": markets}
        )
    return {
        "id": event_id,
        "sport_key": sport_key,
        "sport_title": "ATP Wimbledon",
        "commence_time": iso(commence),
        "home_team": p1,
        "away_team": p2,
        "bookmakers": bookmakers,
    }


def historical_body(snapshot: datetime, events: list[dict[str, Any]]) -> bytes:
    return json.dumps(
        {
            "timestamp": iso(snapshot),
            "previous_timestamp": None,
            "next_timestamp": None,
            "data": events,
        }
    ).encode()


def live_body(events: list[dict[str, Any]]) -> bytes:
    return json.dumps(events).encode()


class ScriptedTransport:
    """Returns queued responses in order and records the (redacted) URLs it saw."""

    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def __call__(self, url: str, timeout: float) -> HttpResponse:
        self.urls.append(url)
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


def ok(body: bytes, last: int = 1, remaining: int = 499) -> HttpResponse:
    return HttpResponse(
        200,
        {
            "x-requests-last": str(last),
            "x-requests-used": "1",
            "x-requests-remaining": str(remaining),
        },
        body,
    )


def fixed_clock(*times: datetime) -> Callable[[], datetime]:
    it = iter(times)
    last = [times[0]]

    def clock() -> datetime:
        with contextlib.suppress(StopIteration):
            last[0] = next(it)
        return last[0]

    return clock
