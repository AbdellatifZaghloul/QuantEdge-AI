"""Phase 3B.1 regressions: each pre-registered contrast is scored on its own pairwise population."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from quantedge.evaluation.metrics import brier_terms, log_loss_terms
from quantedge.odds.devig import proportional
from quantedge.odds.identity import PlayerResolver, link_events, load_tournament_aliases
from quantedge.odds.pilot import pilot_population, run_pilot
from quantedge.odds.preregistration import load_protocol
from quantedge.odds.tables import event_id
from tests.integration.test_odds_pilot import HOLDOUT, NAMES, PROTOCOL_PATH, canonical

START = datetime(2022, 6, 28, 12, 0, tzinfo=UTC)
FAV = {"pinnacle": 1.40, "bookb": 1.50, "bookc": 1.45}


def scenario(coverage: dict[str, set[int]]) -> dict[str, object]:
    """Run the pilot on four synthetic matches; ``coverage``: bookmaker -> covered match indices."""
    protocol, sha = load_protocol(PROTOCOL_PATH)
    matches, tournaments, eligibility, players = canonical()
    matches = matches.sort("match_key")
    predictions = matches.select(
        "match_key",
        "tournament_start_date",
        pl.col("a_won").cast(pl.Int8).alias("y"),
        pl.lit(0.6).alias("p_elo_overall_recal"),
        pl.lit(0.55).alias("p_rank_recal"),
    )
    names = {k[1]: v[0] for k, v in load_tournament_aliases().items() if k[0] == "the_odds_api"}
    population = pilot_population(matches, tournaments, eligibility, predictions, protocol, names)
    events, quotes = [], []
    for i, m in enumerate(matches.iter_rows(named=True)):
        eid = event_id("the_odds_api", f"ev{i}")
        p1, p2 = " ".join(NAMES[m["player_a_id"]]), " ".join(NAMES[m["player_b_id"]])
        events.append(
            {
                "event_id": eid,
                "competition_key": "tennis_atp_wimbledon",
                "participant_1": p1,
                "participant_2": p2,
                "commence_time_utc": START,
                "commence_time_earliest_utc": START,
            }
        )
        observed = START - timedelta(minutes=70)
        for book, covered in coverage.items():
            if i not in covered:
                continue
            fav = FAV[book]
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
                        "commence_time_utc": START,
                        "timing_class": "timestamped",
                        "quality_flags": "",
                    }
                )
    events_df = pl.DataFrame(events)
    quotes_df = pl.DataFrame(quotes)
    appearances = pl.DataFrame(
        {"player_id": list(NAMES), "tournament_start_date": [date(2022, 5, 1)] * len(NAMES)}
    )
    links = link_events(
        events_df,
        matches,
        tournaments,
        PlayerResolver(players, appearances, "the_odds_api"),
        provider="the_odds_api",
    )
    report = run_pilot(
        protocol=protocol,
        protocol_sha256=sha,
        population=population,
        events=events_df,
        quotes=quotes_df,
        links=links,
        predictions=predictions,
        holdout_start=HOLDOUT,
    )
    return {"report": report, "matches": matches}


def contrast(report: dict[str, object], name: str) -> dict[str, object]:
    return report["contrasts"][name]  # type: ignore[index, no-any-return]


ALL = {0, 1, 2, 3}


def test_contrasts_are_the_preregistered_pairs() -> None:
    report = scenario({"pinnacle": ALL, "bookb": ALL})["report"]
    assert set(report["contrasts"]) == {  # type: ignore[index]
        "elo-market_primary",
        "rank-market_primary",
        "elo-market_consensus",
    }


def test_only_one_bookmaker_available() -> None:
    """Consensus (min 2 books) is impossible, but primary contrasts must use all 4 matches."""
    report = scenario({"pinnacle": ALL})["report"]
    assert contrast(report, "elo-market_primary")["n_scored"] == 4
    assert contrast(report, "rank-market_primary")["n_scored"] == 4
    consensus = contrast(report, "elo-market_consensus")
    assert consensus["n_scored"] == 0 and consensus["excluded_missing_reference"] == 4


def test_primary_available_consensus_unavailable_for_some() -> None:
    # pinnacle everywhere (primary); a second book only on matches 0-1 -> consensus on 2 matches.
    report = scenario({"pinnacle": ALL, "bookb": {0, 1}})["report"]
    assert report["reference"]["bookmaker"] == "pinnacle"  # type: ignore[index]
    assert contrast(report, "elo-market_primary")["n_scored"] == 4
    assert contrast(report, "elo-market_consensus")["n_scored"] == 2
    assert report["supplementary_common_population"]["n_common"] == 2  # type: ignore[index]


def test_consensus_available_primary_unavailable_for_some() -> None:
    # pinnacle 3/4 (75 % < 80 %) -> outcome-blind fallback to bookc, the only book at 100 %.
    report = scenario({"pinnacle": {1, 2, 3}, "bookb": {0, 1, 2}, "bookc": ALL})["report"]
    assert report["reference"]["bookmaker"] == "bookc"  # type: ignore[index]
    assert contrast(report, "elo-market_primary")["n_scored"] == 4
    assert contrast(report, "elo-market_consensus")["n_scored"] == 4
    thin = scenario({"pinnacle": ALL, "bookb": ALL, "bookc": {0}})["report"]
    assert contrast(thin, "elo-market_consensus")["n_scored"] == 4


def test_partial_coverage_reports_exact_exclusions() -> None:
    report = scenario({"pinnacle": {0, 1, 2, 3}, "bookb": {2, 3}, "bookc": {3}})["report"]
    primary = contrast(report, "elo-market_primary")
    assert (primary["n_population"], primary["n_scored"]) == (4, 4)
    assert primary["excluded_missing_model"] == 0 and primary["excluded_missing_reference"] == 0
    consensus = contrast(report, "elo-market_consensus")
    assert consensus["n_scored"] == 2 and consensus["excluded_missing_reference"] == 2


def test_pairwise_values_match_a_direct_computation() -> None:
    out = scenario({"pinnacle": ALL})
    report, matches = out["report"], out["matches"]
    y = matches["a_won"].cast(pl.Float64).to_numpy()  # type: ignore[union-attr]
    p_market = np.array(
        [
            proportional((1.40, 3.0)).probabilities[0]
            if a
            else proportional((3.0, 1.40)).probabilities[0]
            for a in matches["a_won"]
        ]
    )  # type: ignore[index]
    p_elo = np.full(4, 0.6)
    expected_ll = float((log_loss_terms(y, p_elo) - log_loss_terms(y, p_market)).mean())
    expected_br = float((brier_terms(y, p_elo) - brier_terms(y, p_market)).mean())
    got = contrast(report, "elo-market_primary")
    assert got["log_loss_diff"]["estimate"] == pytest.approx(expected_ll)  # type: ignore[index]
    assert got["brier_diff"]["estimate"] == pytest.approx(expected_br)  # type: ignore[index]
    # Orientation: the market favourite is always the recorded winner, so its probability is high.
    assert got["metrics"]["reference"]["log_loss"] < got["metrics"]["model"]["log_loss"]  # type: ignore[index]
