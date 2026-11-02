# First real historical odds capture — ingestion defect and data-quality probe

Date: 2026-11-02.

**The capture (owner-run):**
- Australian Open 2022, `tennis_atp_aus_open_singles`, h2h, region `eu`.
- Requested `2022-01-18T12:00:00Z`.
- Capture `cap1_886e5d73887a401dcee13ffe7b883e5f`, 10 credits (19,990 remaining).

**This review:**
- Done entirely offline: zero network calls (sockets disabled during the build), **zero credits spent**.
- The raw body and capture log were byte-identical before and after. The body SHA-256 matches the recorded
  `c8dd579f…`.
- No match outcomes were joined. No evaluation was run.
- The frozen `market-benchmark-v1` protocol is unchanged.
- This document contains aggregate statistics only. No prices, player names or provider event IDs.

## 1. Root cause

- `quantedge odds build-tables` failed with Pandera `SchemaErrors`: two provider prices were exactly `1.0`, and the
  canonical `odds_quotes` schema correctly requires `decimal_odds > 1.0`.
- The normaliser passed every provider price straight into canonical rows, so one invalid observation made the
  whole build fail.
- A missing or non-numeric price would have crashed earlier, in the normaliser's `float(outcome["price"])`.
- The schema was right. The pipeline lacked a way to set aside invalid observations without losing them.

**The two `1.0` prices:**
- **Where:** one *pre-match* event (it started about 14.5 h after the snapshot), from **two different bookmakers**.
- **What:** in each case one selection of that bookmaker's own h2h market, the other side priced 22–23. They are not
  one two-way market.
- **Why invalid:** a decimal price of 1.0 returns nothing to a winning backer, so it is structurally invalid, not
  merely suspicious.

## 2. Fix (no schema weakening)

- **Strict price parsing** (`theoddsapi.parse_decimal_price`), never coercing or repairing. The reasons are:
  - `price_missing`;
  - `price_not_numeric` (strings, booleans, objects);
  - `price_non_finite`;
  - `price_not_above_one`.
- **Quarantine table** `odds_quote_quarantine`, a new table; the existing schemas are unchanged.
  - **Contents:** quote ID, reason, event, bookmaker, market, selection, line, raw provider value, parsed value, all
    timestamps, stream, licence class and the capture's SHA-256.
  - **Location:** next to the canonical tables under the gitignored `data/processed/…`, together with
    `build_counts.json`.
- **Deterministic IDs:** valid quote IDs are unchanged; quarantined ones are keyed on the raw provider value.
- **Idempotent:** rebuilding gives identical tables and counts.
- **Downstream protection:** surviving siblings of a quarantined selection stay in `odds_quotes` with the new flag
  `market_incomplete_after_quarantine`.
  - The selector then sees one selection for that bookmaker and observation, and reports `incomplete_market`.
  - No probability can be formed from a half market.
  - Point-in-time rules, bookmaker identity, timestamps and provenance are unchanged.
- **Short valid prices** (e.g. 1.01) are kept in `odds_quotes` with the existing `odds_out_of_range` flag. They are
  not quarantined.

## 3. Before and after (real capture)

| Count | Before | After |
|---|---|---|
| Events | — (build failed) | 31 |
| Raw quotes | 512 | 512 |
| Canonical valid quotes | — (build failed) | **510** |
| Quarantined | — | **2** (`price_not_above_one`: 2) |
| Valid siblings flagged incomplete | — | 2 |

## 4. Data-quality probe (aggregate, no outcomes)

| Item | Finding |
|---|---|
| Snapshot | One provider snapshot at `11:55:00Z` (5 min before the requested time; neighbours at 11:45 and 12:05, i.e. 10-minute cadence as documented pre-Sept 2022). No snapshot later than requested |
| Bookmakers (`eu`) | 11: betclic, betfair, intertops, marathonbet, matchbook, nordicbet, onexbet, pinnacle, sport888, unibet, williamhill. Per event: 3 to 10 h2h books (median 8) |
| **Pinnacle** | Present on **1 of 31 events**, and that event had **already started** at the snapshot (blocked by the in-play rule). **Admissible pre-match Pinnacle coverage: 0 %** |
| Markets | `h2h` (452 quotes) and `h2h_lay` (58 exchange lay quotes from betfair and matchbook, flagged `unknown_market` and never used as match-winner prices) |
| h2h completeness | 227 bookmaker-market observations: 225 complete two-way, 2 incomplete (the quarantine siblings) |
| `last_update` | Present on 452/452 h2h quotes. Age at the snapshot is 4.6–10.3 min (median 4.6). None later than the observation |
| Timing flags | 14 quotes on the 1 already-started event flagged `observed_after_commence`. No commence-time changes (single snapshot). 3 quotes at ≤ 1.01 flagged `odds_out_of_range` |
| Starts | 10:34 UTC on Jan 18 to 00:00 UTC on Jan 20 |
| Pre-registered timing (decision = earliest start − 60 min) | **0 events usable at 15, 30 or 60 min freshness.** Quote age at decision is 670–2,115 min (median 940); the one in-play event has no quote before its cutoff |

## 5. Scientific usability

**Usable as intended**, i.e. as a *provider-availability probe*:
- it identifies which books price these events;
- it shows that `last_update` is present and fresh relative to snapshots;
- it shows names and start times behave as expected.

**Not usable for the pre-registered comparison**, by design:
- a single mid-day snapshot cannot satisfy the 30-minute freshness rule for decisions taken hours later;
- the planner flagged this in advance (one snapshot per day is a floor that fails freshness);
- the strict selector correctly returns `stale` for every event and creates no probability.

## 6. Concerns before any further acquisition (requires owner decision)

1. **Pinnacle is effectively absent pre-match** in this snapshot.
   - If that holds in the other probes, the pre-registered outcome-blind fallback applies: the highest-availability
     bookmaker (here betclic or nordicbet, each on 30 of 31 events).
   - This is permitted by `market-benchmark-v1` and is not a protocol change. It should be confirmed on the
     remaining probe snapshots before any pilot budget is approved.
2. **Snapshots near decision times are required.** The real pilot cost is closer to the planner's upper range than
   its floor.
3. The h2h_lay and exchange books need no action now (lay quotes are excluded). Exchange back prices carry
   commission semantics to note in later phases.
4. The other three probe snapshots (RG, Wimbledon, US Open 2022) are **not** approved and were **not** requested.

## 7. Tests

New `tests/integration/test_odds_quarantine.py`, synthetic only:

| Area | Tests |
|---|---|
| Invalid prices | Exactly two `1.0` prices quarantined with provenance and counts; a 1.01 price kept and flagged |
| Half markets | An incomplete h2h market after quarantine yields no paired quote or probability |
| Unchanged behaviour | All-valid input gives unchanged output and an empty quarantine |
| Reprocessing | Idempotent; raw body and log unchanged |
| Fully invalid capture | Empty canonical quotes that still validate; explicit selector status |
| Malformed values | NaN, ±inf, string, bool and missing prices each with an explicit reason |
| Parser | 13-case parser table |

Totals:
- 284 tests passed locally (Windows), including real-data tests.
- 273 in the synthetic suite.
- `ruff check .`, `ruff format --check .` and `mypy --strict src/quantedge` all clean.
