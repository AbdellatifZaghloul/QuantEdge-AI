"""Pre-registration guards, the offline planner and the pilot comparison (synthetic data only)."""

from __future__ import annotations

import socket
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from quantedge.odds.identity import PlayerResolver, link_events, load_tournament_aliases
from quantedge.odds.pilot import outcome_blind_guard, pilot_population, run_pilot
from quantedge.odds.planner import build_plan
from quantedge.odds.preregistration import (
    ProtocolViolationError,
    assert_within_window,
    choose_reference,
    load_protocol,
)
from quantedge.odds.tables import event_id
from tests.conftest import REPO_ROOT
from tests.leakage.scenario import frame, mk

PROTOCOL_PATH = REPO_ROOT / "configs" / "preregistration" / "market_benchmark_v1.yaml"
HOLDOUT = date(2025, 1, 1)
WIM = "sackmann:2022-540"
NAMES = {f"sackmann:{700 + i}": (f"Given{i}", f"Family{i}") for i in range(8)}


def test_protocol_loads_and_is_fingerprinted() -> None:
    protocol, sha = load_protocol(PROTOCOL_PATH)
    assert protocol.protocol_id == "market-benchmark-v1" and len(sha) == 64
    assert load_protocol(PROTOCOL_PATH)[1] == sha
    assert protocol.population.tournament_start_to == date(2022, 12, 31)
    assert protocol.timing.decision_lead_minutes == 60
    assert protocol.timing.sensitivity_max_staleness_minutes == (15, 60)
    assert protocol.market.primary_reference_bookmaker == "pinnacle"


def test_window_guard_refuses_late_check_and_holdout() -> None:
    protocol, _ = load_protocol(PROTOCOL_PATH)
    ok = pl.DataFrame({"tournament_start_date": [date(2021, 6, 28)]})
    assert_within_window(ok, protocol, holdout_start=HOLDOUT)
    for bad in (date(2023, 7, 3), date(2025, 6, 30), date(2019, 7, 1)):
        with pytest.raises(ProtocolViolationError):
            assert_within_window(
                pl.DataFrame({"tournament_start_date": [bad]}), protocol, holdout_start=HOLDOUT
            )


def test_reference_choice_is_outcome_blind_and_predeclared() -> None:
    protocol, _ = load_protocol(PROTOCOL_PATH)
    adequate = pl.DataFrame({"bookmaker_key": ["pinnacle", "zbook"], "availability": [0.85, 0.99]})
    assert choose_reference(adequate, protocol) == ("pinnacle", "preferred_bookmaker_adequate")
    thin = pl.DataFrame(
        {"bookmaker_key": ["pinnacle", "bbook", "abook"], "availability": [0.5, 0.9, 0.9]}
    )
    assert choose_reference(thin, protocol) == ("abook", "fallback_highest_availability")
    with pytest.raises(ProtocolViolationError, match="outcome-blind"):
        choose_reference(adequate.with_columns(pl.lit(1).alias("y")), protocol)
    with pytest.raises(ProtocolViolationError):
        outcome_blind_guard(pl.DataFrame({"a_won": [True]}))


# ----------------------------------------------------------------------------- canonical fixture


def canonical() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    ids = list(NAMES)
    rows = [
        mk(WIM, "R128", ids[2 * i], ids[2 * i + 1], date(2022, 6, 27), date(2022, 7, 1))
        for i in range(4)
    ]
    matches = frame(rows).with_columns(
        pl.col("match_day_lo").alias("tournament_start_date"),
        pl.lit("tour_main").alias("draw_category"),
    )
    tournaments = pl.DataFrame(
        {"tournament_id": [WIM], "name": ["Wimbledon"], "level": ["G"], "nominal_days": [15]}
    ).with_columns(pl.col("nominal_days").cast(pl.Int16))
    eligibility = matches.select("match_key", pl.lit(True).alias("eval_eligible"))
    players = pl.DataFrame(
        {
            "player_id": ids,
            "name_first": [v[0] for v in NAMES.values()],
            "name_last": [v[1] for v in NAMES.values()],
        }
    )
    return matches, tournaments, eligibility, players


def test_planner_is_offline_and_respects_probe_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid(*args: object, **kwargs: object) -> None:
        raise AssertionError("planner attempted a network call")

    monkeypatch.setattr(socket, "create_connection", forbid)
    protocol, _ = load_protocol(PROTOCOL_PATH)
    matches, tournaments, eligibility, _ = canonical()
    plan = build_plan(matches, tournaments, eligibility, protocol, probe_max_credits=40)
    assert plan["network_calls_made"] == 0 and plan["credits_spent"] == 0
    probe = plan["probe"]
    assert probe["within_budget"] and probe["total_credits"] <= 40
    assert [r["sport_key"] for r in probe["requests"]] == ["tennis_atp_wimbledon"]
    assert probe["requests"][0]["date_param_utc"] == "2022-06-28T12:00:00Z"
    pilot = plan["pilot_estimate"]
    assert pilot["expected_eligible_population"] == 4
    assert pilot["credits_upper_bound"] == 15 * 1 + 4 * 10 and not pilot["within_budget"]
    assert "returned quotes are unknown" in pilot["returns"]
    with pytest.raises(ValueError, match="40 credits"):
        build_plan(matches, tournaments, eligibility, protocol, probe_max_credits=41)


def _odds(matches: pl.DataFrame, books: dict[str, float]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Synthetic normalised events and quotes; each book has a fixed favourite price."""
    start = datetime(2022, 6, 28, 12, 0, tzinfo=UTC)
    events, quotes = [], []
    for i, m in enumerate(matches.iter_rows(named=True)):
        eid = event_id("the_odds_api", f"ev{i}")
        p1 = " ".join(NAMES[m["player_a_id"]])
        p2 = " ".join(NAMES[m["player_b_id"]])
        events.append(
            {
                "event_id": eid,
                "competition_key": "tennis_atp_wimbledon",
                "participant_1": p1,
                "participant_2": p2,
                "commence_time_utc": start,
                "commence_time_earliest_utc": start,
            }
        )
        observed = start - timedelta(minutes=70)
        for book, fav in books.items():
            if book == "pinnacle" and i == 0:
                continue  # pinnacle misses one of four events -> 75% availability
            o_a, o_b = (fav, 3.0) if m["a_won"] else (3.0, fav)
            for name, price in ((p1, o_a), (p2, o_b)):
                quotes.append(
                    {
                        "quote_id": f"{book}-{i}-{name}",
                        "event_id": eid,
                        "bookmaker_key": book,
                        "market": "match_winner",
                        "selection_name": name,
                        "decimal_odds": price,
                        "observed_at_utc": observed,
                        "market_last_update_utc": observed,
                        "commence_time_utc": start,
                        "timing_class": "timestamped",
                        "quality_flags": "",
                    }
                )
    return pl.DataFrame(events), pl.DataFrame(quotes)


def test_pilot_end_to_end_on_synthetic_data() -> None:
    protocol, sha = load_protocol(PROTOCOL_PATH)
    matches, tournaments, eligibility, players = canonical()
    predictions = matches.select(
        "match_key",
        "tournament_start_date",
        pl.col("a_won").cast(pl.Int8).alias("y"),
        pl.lit(0.6).alias("p_elo_overall_recal"),
        pl.lit(0.55).alias("p_rank_recal"),
    )
    names = {k[1]: v[0] for k, v in load_tournament_aliases().items() if k[0] == "the_odds_api"}
    population = pilot_population(matches, tournaments, eligibility, predictions, protocol, names)
    assert population.height == 4
    events, quotes = _odds(matches, {"pinnacle": 1.4, "bookb": 1.5, "bookc": 1.45})
    appearances = pl.DataFrame(
        {"player_id": list(NAMES), "tournament_start_date": [date(2022, 5, 1)] * 8}
    )
    links = link_events(
        events,
        matches,
        tournaments,
        PlayerResolver(players, appearances, "the_odds_api"),
        provider="the_odds_api",
    )
    assert links["status"].to_list() == ["linked"] * 4
    report = run_pilot(
        protocol=protocol,
        protocol_sha256=sha,
        population=population,
        events=events,
        quotes=quotes,
        links=links,
        predictions=predictions,
        holdout_start=HOLDOUT,
    )
    # Pinnacle covers 3/4 = 75% < 80% pre-registered adequacy -> outcome-blind fallback.
    assert report["reference"] == {"bookmaker": "bookb", "rule": "fallback_highest_availability"}
    assert report["protocol_sha256"] == sha
    primary = report["primary"]
    assert primary["n_common"] == 4 and set(primary["metrics"]) == {
        "market_primary",
        "elo",
        "rank",
        "market_consensus",
    }
    assert "brier_diff::elo-market_primary" in primary["bootstrap_95"]
    assert set(report["sensitivity_staleness"]) == {"15", "60"}
    # Quotes are 70 minutes before start = 10 minutes before the decision: fresh at 15 and 60.
    assert report["sensitivity_staleness"]["15"]["n_common"] == 4
    assert report["quote_age_minutes_reference"][0.5] == pytest.approx(10.0)


def test_pilot_refuses_population_outside_window() -> None:
    protocol, sha = load_protocol(PROTOCOL_PATH)
    bad = pl.DataFrame({"match_key": ["m"], "tournament_start_date": [date(2023, 7, 3)]})
    with pytest.raises(ProtocolViolationError):
        run_pilot(
            protocol=protocol,
            protocol_sha256=sha,
            population=bad,
            events=pl.DataFrame(),
            quotes=pl.DataFrame(),
            links=pl.DataFrame(),
            predictions=pl.DataFrame(),
            holdout_start=HOLDOUT,
        )
