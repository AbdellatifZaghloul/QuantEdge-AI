# Four-Grand-Slam historical odds probe — outcome-blind quality audit

Date: 2026-11-02.

**The probe:** The Odds API, `eu` region, h2h. Four pre-registered 2022 snapshots (D-29), each requested for day 2
of the main draw at 12:00 UTC. 40 credits used in total; 19,960 remain.

**This audit:**
- Fully offline: sockets disabled in every script; **0 network calls, 0 credits**.
- No outcomes joined and no model comparison run.
- 2023–24 and 2025+ untouched.
- `market-benchmark-v1` unchanged (SHA-256 `e3d3f642…67da21`).
- Aggregate statistics only; no prices, player names or provider event IDs.

## 1. Integrity

- **Captures:** 4 log records and 4 content-addressed bodies. Every body was re-hashed on read and matches its
  recorded SHA-256.
- **Credentials:** no API key in any stored request parameter.
- **Snapshots:** each request returned the snapshot at `11:55:00Z` (≤ requested 12:00, as documented), with
  neighbours at ±10 min.
- **Rebuild:** offline rebuild reproduces the stated totals exactly.

| Item | Count |
|---|---|
| Events | 168 |
| Raw quotes | 2,960 |
| Valid canonical quotes | 2,949 |
| Quarantined (`price_not_above_one`) | 11 |
| Valid siblings flagged incomplete | 11 |

## 2. Per tournament

"Pre-match" means the scheduled start is after the snapshot. "Complete" means both selections from one bookmaker in
one observation.

| | AO 2022 | RG 2022 | Wimbledon 2022 | US Open 2022 |
|---|---|---|---|---|
| Events (pre-match / started) | 31 (30 / 1) | 41 (29 / 12) | 48 (39 / 9) | 48 (48 / 0) |
| Raw / valid / quarantined quotes | 512 / 510 / 2 | 590 / 590 / 0 | 952 / 949 / 3 | 906 / 900 / 6 |
| Exchange lay quotes (excluded) | 58 | 48 | 100 | 100 |
| h2h observations: complete / total | 225 / 227 | 271 / 271 | 423 / 426 | 397 / 403 |
| Complete pre-match h2h observations | 220 | 232 | 375 | 397 |
| Bookmakers | 11 | 11 | 12 | 12 |
| **Pinnacle complete pre-match** | **0 / 30** (only on the 1 started event) | **29 / 29** | **39 / 39** | **31 / 48** |
| Outcome-blind reference, this snapshot only (diagnostic) | fallback → betclic | Pinnacle (adequate) | Pinnacle (adequate) | fallback → betsson |
| `last_update` present (h2h) | 452 / 452 | 542 / 542 | 849 / 849 | 800 / 800 |
| `last_update` age at snapshot, min (median, max) | 4.6 (4.6, 10.3) | 4.3 (4.6, 18.0) | 4.2 (4.7, 19.1) | 4.6 (4.7, 7.2) |
| Start − snapshot (h), min / median / max | −1.3 / 21.3 / 36.1 | −18.4 / 3.2 / 21.1 | −18.1 / 2.4 / 22.1 | 3.1 / 8.1 / 27.1 |
| Quotes flagged `observed_after_commence` | 14 | 98 | 127 | 0 |
| Quotes ≤ 1.01 (valid, flagged) | 3 | 9 | 9 | 20 |

Notes:
- No source timestamp was later than its observation.
- No commence time changed. Each event appears in only one snapshot, so reschedules cannot be detected.
- Events that had "started" up to 18 h before the snapshot (RG and Wimbledon) are probably suspended or resumed
  matches. They are in-play and inadmissible.

**Pooled across the 146 pre-match events** (complete h2h availability, outcome-blind):

| Bookmaker | Share of events |
|---|---|
| sport888 | 0.945 |
| unibet | 0.945 |
| intertops | 0.938 |
| onexbet | 0.938 |
| **pinnacle** | **0.678** |
| betclic | 0.651 |
| marathonbet | 0.527 |
| betonlineag | 0.486 |
| matchbook | 0.473 |
| mybookieag | 0.459 |
| williamhill | 0.425 |
| betfair | 0.390 |
| betsson | 0.322 |
| nordicbet | 0.205 |

- Every pre-match event has ≥ 3 complete books (median 8), so the consensus (≥ 2 books) is structurally feasible
  everywhere.
- **Diagnostic only:**
  - pooled Pinnacle (67.8 %) is below the pre-registered 80 % adequacy threshold;
  - if that held at decision time, the frozen fallback would select the highest-availability book (sport888 and unibet
    tie, so alphabetically sport888).
- The protocol decides the reference on the actual pilot population at the primary timing. **No reference choice is
  made from this probe.**

## 3. Structurally valid vs scientifically usable

| | Count |
|---|---|
| Structurally valid canonical quotes | 2,949 |
| Excluded from match-winner probabilities: exchange lay (`h2h_lay`, market `other`) | 306 |
| Excluded: in-play (`observed_after_commence`) | 239 |
| **Usable under the frozen rule** (decision = earliest scheduled start − 60 min, source age ≤ 30 min) | **0 events, 0 bookmaker markets** at all four Slams |

Why nothing is usable:
- At each decision time the newest admissible quote is hours old (AO median 940 min).
- Selector statuses:
  - AO: 220 stale, 1 no quote before cutoff;
  - RG: 215 stale, 14 no quote;
  - Wimbledon: 327 stale, 13 no quote;
  - US Open: 397 stale.
- This is the expected behaviour of a one-snapshot availability probe, not a data defect. The probe answered its
  questions (bookmakers, Pinnacle, timestamp quality), but cannot support the comparison.

Exchange lay exclusion is now pinned by an explicit regression test (`tests/leakage/test_lay_exclusion.py`).

## 4. Decision-time snapshot requirement

- A historical call returns the closest snapshot ≤ the requested time, at a 10-minute cadence before Sept 2022.
- Quote age at decision ≈ (decision − snapshot) + market lag (median about 4.6 min).
- If decisions within a **10-minute span** share one call requested at the span's earliest decision, the typical age
  is ≤ 10 + 10 + 5 ≈ 25 min. That is within 30 min, though markets lagging ≥ 10 min may still be stale.
- Scheduled starts are strongly clustered: 54 distinct start times for 146 events. The US Open uses only on-the-hour
  times, with up to 16 events per time, which suggests session-level scheduled starts.

| | AO | RG | Wimbledon | US Open | Total |
|---|---|---|---|---|---|
| 10-min decision clusters for already-visible pre-match events | 11 | 10 | 12 | 9 | **42** (≈ 0.29 per event) |

**Full pilot (10 editions, 1,228 eligible main-draw matches, h2h × eu at 10 credits per odds call):**

| Basis | Odds snapshots | Credits |
|---|---|---|
| Ratio from this probe (0.29 per event; dense early rounds, so optimistic) | ~354 | ~3,540 |
| **Central:** round-structure model (≈ 70–80 decision clusters per edition; later rounds ≈ one per match) | ~700–800 | **~7,000–8,000** |
| Upper bound: one per match | 1,228 | 12,280 |
| Schedule discovery: historical events calls (1 credit each, about 15 per edition) | — | ~150 |

The central estimate including discovery is about 7,200–8,200 credits. It fits within the 19,960 remaining, but most
of the monthly plan would be used.

## 5. Recommended staged acquisition (each stage needs explicit owner approval)

All criteria are outcome-blind and fixed now. Linking to canonical matches uses identity only, never results.

| Stage | Scope | Credit ceiling |
|---|---|---|
| **A** | Wimbledon 2022: decision-time snapshots for the 39 pre-match events already scheduled in the 28 Jun snapshot (12 clusters); no discovery cost | **130** |
| **B** | AO, RG, US Open 2022 visible schedules (30 clusters) | **320** |
| **C** | One full edition (Wimbledon 2022 main draw): daily events calls plus 10-min clusters | **1,300** |
| **D** | Remaining 9 editions, one edition at a time, each re-checked against the go criteria | **1,300 per edition; 9,000 cumulative** |

The programme ceiling for A–D is **10,750 credits**. After D, the single confirmatory `pilot-compare` run (A6) is made
under `market-benchmark-v1`.

> **Erratum (2026-11-02):** the Stage D row is inconsistent. 9 × 1,300 = 11,700, not 9,000. The 10,750 total matches
> 9,000 (i.e. 1,000 per edition). The owner chooses either 1,300 per edition (11,700 cumulative, programme 13,450) or
> 1,000 per edition (9,000, programme 10,750). See `stage-a-wimbledon-2022-preflight.md` §9. Stage A: the exact
> manifest needs 9 calls (90 credits), not 12 clusters.

**Go criteria after each stage** (all required; otherwise stop, report, no further spend):
1. ≥ 80 % of planned events have ≥ 1 bookmaker `available` at 30-min freshness.
2. ≥ 70 % have ≥ 2 available books (consensus feasible).
3. Median quote age at decision ≤ 20 min.
4. Every returned snapshot ≤ requested time; no source timestamp later than its observation.
5. ≥ 90 % of events link uniquely to canonical matches; no ambiguous or duplicate links.
6. No new quarantine reason class or structural anomaly.

**Stop immediately** if any call returns non-200, the quota differs from the expected cost, or credit use exceeds the
stage ceiling.

## 6. Methodological limitations

- **Sample:** one snapshot per Slam, all on day 2. Early rounds dominate, so later-round density and per-edition cost
  are modelled, not observed.
- **Scheduled starts:** these look like session or "not before" times. Anchoring decisions on them is conservative
  (decisions are earlier than necessary) but may lower quote informativeness for later matches. Reschedules are
  unobservable from single snapshots.
- **Availability:** structural availability at a snapshot is not decision-time availability. Pinnacle adequacy and
  the reference choice must be computed on the actual pilot population under the primary timing.
- **2020–2021:** editions (including RG 2020 in October and COVID-era formats) may differ in coverage and timing from
  2022.
- **Region and books:** only the `eu` region was examined. Exchange *back* prices (betfair, matchbook h2h) are treated
  as bookmaker quotes; commission is irrelevant to the probabilities but matters for any later execution analysis.
- **Freshness margin:** pre-Sept-2022 10-minute cadence plus market lags up to about 19 min means some markets will
  remain stale even with decision-time snapshots. That is measured by criterion 3, not assumed away.

## 7. Next step

**Owner approval requested for Stage A only** (Wimbledon 2022, ≤ 130 credits, request list generated offline from the
existing snapshot). No paid call has been made since the four probe captures.
