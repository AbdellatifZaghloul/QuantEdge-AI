"""The Odds API (v4) adapter: budgeted, retrying client + pure normaliser.

Verified against the official v4 documentation on 2026-11-01 (docs/reviews/phase-3a.md §2):

- ``/v4/sports`` and ``/v4/sports/{sport}/events``: no quota cost.
- ``/v4/sports/{sport}/odds``: ``markets x regions`` credits.
- ``/v4/historical/sports/{sport}/odds?date=``: ``10 x markets x regions`` credits; response wrapper
  ``{timestamp, previous_timestamp, next_timestamp, data}`` where ``timestamp`` is the closest
  snapshot at or before ``date``.
- Quota headers ``x-requests-remaining``, ``x-requests-used``, ``x-requests-last``.
- Market-level ``last_update`` = last time the provider saw the market's odds; the bookmaker-level
  ``last_update`` is deprecated and ignored here.

Safety: the API key comes only from the environment, is never stored or logged, and is redacted
from errors. Every chargeable request is checked against a per-run credit budget *before* it is
sent, and historical requests additionally require explicit approval.
"""

from __future__ import annotations

import json
import math
import os
import random
import time
import urllib.parse
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from quantedge.core.http import HttpResponse, Transport, urllib_transport
from quantedge.core.lineage import LicenceClass
from quantedge.odds.provider import (
    CaptureRequest,
    NormalizedEvent,
    NormalizedQuote,
    NormalizedSnapshot,
    RawCapture,
)

PROVIDER = "the_odds_api"
API_KEY_ENV = "QE_ODDS_API_KEY"
MARKET_MAP = {"h2h": "match_winner", "totals": "total_games", "spreads": "game_handicap"}
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class OddsApiError(RuntimeError):
    """Raised for non-retryable API errors (messages never contain the API key)."""


class MissingCredentialError(OddsApiError):
    """Raised when the API key environment variable is not set."""


class BudgetExceededError(OddsApiError):
    """Raised before sending a request that would exceed the configured credit budget."""


class PaidCallNotApprovedError(OddsApiError):
    """Raised for historical (chargeable) calls without explicit approval."""


class OddsApiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str = "https://api.the-odds-api.com"
    regions: tuple[str, ...] = ("eu",)
    markets: tuple[str, ...] = ("h2h",)
    odds_format: str = "decimal"
    date_format: str = "iso"
    timeout_s: float = 30.0
    max_retries: int = 4
    backoff_base_s: float = 2.0
    backoff_max_s: float = 60.0
    max_credits_per_run: int = 0  # 0 = no chargeable calls at all


def cost_live_odds(markets: Sequence[str], regions: Sequence[str]) -> int:
    return len(set(markets)) * len(set(regions))


def cost_historical_odds(markets: Sequence[str], regions: Sequence[str]) -> int:
    return 10 * len(set(markets)) * len(set(regions))


def parse_utc(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {value!r}")
    return parsed.astimezone(UTC)


def parse_decimal_price(outcome: dict[str, Any]) -> tuple[float, str, str | None]:
    """Strictly parse a decimal price: ``(value, raw, issue)``; never coerces or repairs.

    ``issue`` is ``None`` for a valid price (finite and > 1.0); otherwise a quarantine reason:
    ``price_missing``, ``price_not_numeric`` (strings, booleans, objects), ``price_non_finite``
    (NaN / infinity), or ``price_not_above_one`` (<= 1.0 cannot be a winning back price).
    """
    if "price" not in outcome or outcome["price"] is None:
        return math.nan, json.dumps(outcome.get("price")), "price_missing"
    value = outcome["price"]
    raw = json.dumps(value, allow_nan=True)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return math.nan, raw, "price_not_numeric"
    price = float(value)
    if not math.isfinite(price):
        return price, raw, "price_non_finite"
    if price <= 1.0:
        return price, raw, "price_not_above_one"
    return price, raw, None


def _redact(text: str, secret: str | None) -> str:
    return text.replace(secret, "***") if secret else text


class TheOddsApiClient:
    """HTTP client with credit budget, approval gate, retries and backoff."""

    def __init__(
        self,
        config: OddsApiConfig,
        *,
        api_key: str | None = None,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        self._api_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random(0)  # noqa: S311 - backoff jitter, not cryptography
        self.credits_spent = 0

    def _key(self) -> str:
        if not self._api_key:
            raise MissingCredentialError(
                f"set {API_KEY_ENV} in the environment (e.g. a local .env that is gitignored)"
            )
        return self._api_key

    def _request(
        self,
        path: str,
        params: dict[str, str],
        *,
        expected_cost: int,
        competition: str | None,
        snapshot: str | None = None,
    ) -> RawCapture:
        if (
            expected_cost > 0
            and self.credits_spent + expected_cost > self.config.max_credits_per_run
        ):
            raise BudgetExceededError(
                f"request would cost {expected_cost} credits; run budget is "
                f"{self.config.max_credits_per_run} with {self.credits_spent} already spent"
            )
        key = self._key()
        query = urllib.parse.urlencode({"apiKey": key, **params})
        url = f"{self.config.base_url}{path}?{query}"
        request = CaptureRequest(
            endpoint=path,
            competition_key=competition,
            params=tuple(sorted(params.items())),
            snapshot_requested_utc=snapshot,
        )
        attempt = 0
        while True:
            captured_at = self._clock()
            try:
                response = self._transport(url, self.config.timeout_s)
            except OSError as exc:
                response = HttpResponse(599, {}, _redact(str(exc), key).encode())
            if response.status == 200:
                break
            if response.status in _RETRY_STATUSES and attempt < self.config.max_retries:
                self._sleep(self._backoff(attempt, response))
                attempt += 1
                continue
            detail = _redact(response.body[:300].decode("utf-8", "replace"), key)
            raise OddsApiError(f"{path} failed with HTTP {response.status}: {detail}")

        def header_int(name: str) -> int | None:
            value = response.header(name)
            try:
                return int(float(value)) if value is not None else None
            except ValueError:
                return None

        last = header_int("x-requests-last")
        self.credits_spent += last if last is not None else expected_cost
        return RawCapture(
            provider=PROVIDER,
            request=request,
            captured_at_utc=captured_at,
            status=200,
            body=response.body,
            credits_last=last,
            credits_used=header_int("x-requests-used"),
            credits_remaining=header_int("x-requests-remaining"),
            regions=tuple(params.get("regions", "").split(",")) if params.get("regions") else (),
        )

    def _backoff(self, attempt: int, response: HttpResponse) -> float:
        retry_after = response.header("retry-after")
        if retry_after is not None:
            try:
                return min(float(retry_after), self.config.backoff_max_s)
            except ValueError:
                pass
        base = self.config.backoff_base_s * 2**attempt
        return float(min(base + self._rng.uniform(0, base / 2), self.config.backoff_max_s))

    # -- free endpoints --------------------------------------------------------------------------
    def sports(self) -> RawCapture:
        return self._request("/v4/sports", {"all": "true"}, expected_cost=0, competition=None)

    def events(self, sport: str) -> RawCapture:
        params = {"dateFormat": self.config.date_format}
        return self._request(
            f"/v4/sports/{sport}/events", params, expected_cost=0, competition=sport
        )

    # -- chargeable endpoints --------------------------------------------------------------------
    def _odds_params(self) -> dict[str, str]:
        return {
            "regions": ",".join(self.config.regions),
            "markets": ",".join(self.config.markets),
            "oddsFormat": self.config.odds_format,
            "dateFormat": self.config.date_format,
        }

    def live_odds(self, sport: str) -> RawCapture:
        cost = cost_live_odds(self.config.markets, self.config.regions)
        return self._request(
            f"/v4/sports/{sport}/odds", self._odds_params(), expected_cost=cost, competition=sport
        )

    def historical_odds(self, sport: str, at: datetime, *, approved: bool) -> RawCapture:
        if not approved:
            raise PaidCallNotApprovedError(
                "historical odds cost 10 x markets x regions credits per call; explicit owner "
                "approval is required"
            )
        stamp = at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = self._odds_params() | {"date": stamp}
        cost = cost_historical_odds(self.config.markets, self.config.regions)
        return self._request(
            f"/v4/historical/sports/{sport}/odds",
            params,
            expected_cost=cost,
            competition=sport,
            snapshot=stamp,
        )


# ---------------------------------------------------------------------------------- normaliser


class TheOddsApiNormalizer:
    name = PROVIDER
    licence_class = LicenceClass.COMMERCIAL_LICENSED

    def normalize(self, capture: RawCapture) -> NormalizedSnapshot:
        payload: Any = json.loads(capture.body.decode("utf-8"))
        historical = capture.request.endpoint.startswith("/v4/historical/")
        if historical:
            if not isinstance(payload, dict) or "timestamp" not in payload:
                raise ValueError("historical response without snapshot timestamp")
            observed = parse_utc(payload["timestamp"])
            events_payload = payload.get("data") or []
        else:
            observed = capture.captured_at_utc
            events_payload = payload
        assert observed is not None
        if not isinstance(events_payload, list):
            raise ValueError("unexpected odds payload shape")
        region = capture.regions[0] if len(capture.regions) == 1 else None
        events: list[NormalizedEvent] = []
        quotes: list[NormalizedQuote] = []
        for event in events_payload:
            commence = parse_utc(event["commence_time"])
            assert commence is not None
            p1, p2 = str(event["home_team"]), str(event["away_team"])
            events.append(
                NormalizedEvent(
                    str(event["id"]),
                    str(event["sport_key"]),
                    event.get("sport_title"),
                    p1,
                    p2,
                    commence,
                )
            )
            for book in event.get("bookmakers") or []:
                for market in book.get("markets") or []:
                    key = str(market["key"])
                    canonical = MARKET_MAP.get(key, "other")
                    for outcome in market.get("outcomes") or []:
                        name = str(outcome["name"])
                        role = (
                            "participant"
                            if canonical in {"match_winner", "game_handicap"}
                            else name.lower()
                            if name.lower() in {"over", "under"}
                            else "other"
                        )
                        point = outcome.get("point")
                        price, price_raw, price_issue = parse_decimal_price(outcome)
                        quotes.append(
                            NormalizedQuote(
                                provider_event_id=str(event["id"]),
                                bookmaker_key=str(book["key"]),
                                bookmaker_title=book.get("title"),
                                region=region,
                                market=canonical,
                                provider_market_key=key,
                                selection_name=name,
                                selection_role=role,
                                line=None if point is None else float(point),
                                decimal_odds=price,
                                commence_time_utc=commence,
                                market_last_update_utc=parse_utc(market.get("last_update")),
                                price_raw=price_raw,
                                price_issue=price_issue,
                            )
                        )
        return NormalizedSnapshot(
            provider=PROVIDER,
            observed_at_utc=observed,
            captured_at_utc=capture.captured_at_utc,
            capture_mode="historical" if historical else "live",
            events=tuple(events),
            quotes=tuple(quotes),
        )
