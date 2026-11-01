"""Point-in-time odds selection: nothing after the decision, nothing in-play, explicit gaps."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
from hypothesis import given, settings
from hypothesis import strategies as st

from quantedge.odds.selector import STATUSES, SelectorConfig, select_quotes

UTC_DT = pl.Datetime("us", "UTC")
START = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)


def quotes(rows: list[dict[str, object]]) -> pl.DataFrame:
    base = {"market": "match_winner", "timing_class": "timestamped", "commence_time_utc": START}
    data = [base | r for r in rows]
    for i, r in enumerate(data):
        r.setdefault("quote_id", f"q{i}")
        r.setdefault("market_last_update_utc", r["observed_at_utc"])
    return pl.DataFrame(data).with_columns(
        pl.col("observed_at_utc", "commence_time_utc", "market_last_update_utc").cast(UTC_DT)
    )


def pair(
    book: str, observed: datetime, o1: float, o2: float, **kw: object
) -> list[dict[str, object]]:
    return [
        {
            "event_id": "e",
            "bookmaker_key": book,
            "observed_at_utc": observed,
            "selection_name": "P One",
            "decimal_odds": o1,
            **kw,
        },
        {
            "event_id": "e",
            "bookmaker_key": book,
            "observed_at_utc": observed,
            "selection_name": "P Two",
            "decimal_odds": o2,
            **kw,
        },
    ]


def decisions(*times: datetime, event: str = "e") -> pl.DataFrame:
    return pl.DataFrame(
        {"event_id": [event] * len(times), "decision_time_utc": list(times)}
    ).with_columns(pl.col("decision_time_utc").cast(UTC_DT))


def test_latest_quote_before_cutoff_is_selected() -> None:
    q = quotes(
        pair("b", START - timedelta(hours=3), 1.5, 2.6)
        + pair("b", START - timedelta(hours=1), 1.6, 2.4)
        + pair("b", START - timedelta(minutes=10), 1.7, 2.2)
    )
    out = select_quotes(
        q, decisions(START - timedelta(minutes=30)), SelectorConfig(max_staleness_minutes=60)
    )
    row = out.row(0, named=True)
    assert (
        row["status"] == "available" and row["odds_1"] == 1.6
    )  # the 10-minutes-before quote is future
    assert row["price_semantics"] == "observed_quote_not_guaranteed_executable"


def test_explicit_statuses() -> None:
    q = quotes(
        [
            *pair("b", START - timedelta(hours=5), 1.5, 2.6),
            {
                "event_id": "e",
                "bookmaker_key": "half",
                "observed_at_utc": START - timedelta(hours=1),
                "selection_name": "P One",
                "decimal_odds": 1.5,
            },
            *pair("late", START - timedelta(minutes=5), 1.5, 2.6),
            *pair("inplay", START + timedelta(minutes=5), 1.2, 4.0),
        ]
    )
    out = select_quotes(
        q, decisions(START - timedelta(minutes=30)), SelectorConfig(max_staleness_minutes=60)
    )
    assert dict(zip(out["bookmaker_key"], out["status"], strict=True)) == {"b": "stale"}
    nothing = select_quotes(q, decisions(START - timedelta(hours=6)))
    assert nothing["status"].to_list() == ["no_quote_before_cutoff"]
    after = select_quotes(q, decisions(START + timedelta(minutes=10)))
    assert after["status"].to_list() == ["decision_after_commence"]
    incomplete = select_quotes(
        q.filter(pl.col("bookmaker_key") == "half"), decisions(START - timedelta(minutes=30))
    )
    assert incomplete["status"].to_list() == ["incomplete_market"]
    untimed = select_quotes(
        q.with_columns(pl.lit("untimed_near_close").alias("timing_class")),
        decisions(START - timedelta(minutes=30)),
    )
    assert untimed["status"].to_list() == ["untimed_not_admissible"]
    assert set(out["status"]) | set(nothing["status"]) <= set(STATUSES)


def test_unknown_event_reports_missing() -> None:
    out = select_quotes(
        quotes(pair("b", START - timedelta(hours=1), 1.5, 2.6)),
        decisions(START - timedelta(minutes=30), event="other"),
    )
    assert out["status"].to_list() == ["no_quote_before_cutoff"]


observations = st.lists(
    st.tuples(
        st.sampled_from(["b1", "b2", "b3"]),
        st.integers(-600, 120),
        st.floats(1.05, 5.0),
        st.floats(1.05, 5.0),
    ),
    min_size=1,
    max_size=25,
)


@settings(max_examples=250, deadline=None)
@given(observations, st.integers(-500, 30))
def test_never_selects_future_or_inplay_and_ignores_future_rows(
    obs: list[tuple[str, int, float, float]], decision_offset: int
) -> None:
    rows: list[dict[str, object]] = []
    for book, minutes, o1, o2 in obs:
        rows += pair(book, START + timedelta(minutes=minutes), o1, o2)
    q = quotes(rows).unique(
        subset=["bookmaker_key", "observed_at_utc", "selection_name"], keep="first"
    )
    t = START + timedelta(minutes=decision_offset)
    config = SelectorConfig(max_staleness_minutes=10_000)
    out = select_quotes(q, decisions(t), config)
    chosen = out.filter(pl.col("status").is_in(["available", "stale"]))
    assert (chosen["observed_at_utc"] <= t).all() and (chosen["observed_at_utc"] < START).all()
    # Rewriting every quote observed after t must not change the selection.
    perturbed = q.with_columns(
        pl.when(pl.col("observed_at_utc") > t)
        .then(pl.col("decimal_odds") * 3)
        .otherwise(pl.col("decimal_odds"))
        .alias("decimal_odds")
    )
    assert select_quotes(perturbed, decisions(t), config).equals(out)
