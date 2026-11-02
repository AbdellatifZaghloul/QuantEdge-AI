# Stage A results — Wimbledon 2022 post-acquisition audit (outcome-blind)

Date: 2026-11-02.

**Scope:**
- Offline only: sockets disabled in every script. **0 network calls, 0 credits.** No `capture-historical` or
  `stage-run --execute`.
- No outcomes, scores or model-vs-market comparisons.
- `market-benchmark-v1` unchanged (SHA-256 `e3d3f642…67da21`).
- The raw store, the Stage A ledger and the manifest are byte-identical before and after this audit (16 files
  re-hashed).
- Aggregate figures only. Events are labelled `E01`–`E39` in decision order. No provider IDs, player names or prices
  appear here.

**Disclosure.** One diagnostic script loaded identity columns (match key, tournament, date, player IDs) of the
canonical development view, which includes 2023–24 rows. This is also the view the existing `odds link` command
uses.
- No outcome column was read.
- 2023–24 rows cannot affect a 2022 link: activity checks only use dates before the event.
- Re-linking restricted to ≤ 2022 reproduces all 179 earlier links exactly (§5).
- `odds link` is now scoped to the inner folds (≤ 2022-12-31), so it never reads late-check or holdout rows.

## 1. Integrity and spend

| Check | Result |
|---|---|
| Capture log records / verified bodies | 13 / 13 (4 probe + 9 Stage A); every SHA-256 matches on read |
| Ledger entries vs manifest | 9 / 9, same request IDs and dates, in order |
| Credits | 10 per call; cumulative **90**; quota 19,960 → 19,870 in exact 10-credit steps |
| Returned snapshots | All exactly as planned (10:55 … 08:55; grid offset :x5 confirmed); none later than requested |
| Planned events present in their snapshot | **39 / 39** (`events_present` = planned for every request) |
| Credentials | No API key in any stored parameter, ledger or output |
| Odds tables rebuilt in memory | Identical to the owner's build: 192 events, 10,314 raw, 10,281 valid, 33 quarantined (all `price_not_above_one`) |

## 2. Scheduled-start changes and the 19 `anchor_moved_events`

**The provider's `commence_time` is a moving estimate, not a fixed schedule.**
- Across 10 snapshots, 36 of 39 events show 2–9 start versions: 96 later changes and 50 earlier changes, from
  −122 to +345 min.
- Several events share identical change sequences, consistent with court-order ("followed by") estimates being
  recomputed as earlier matches run long or finish early.
- 50 versions carry non-zero seconds. 49 of those were observed at or after that start, i.e. the provider stamps an
  actual start once a match is in play.
- Day-3 events listed at 10:00 UTC (session start) moved +120 to +345 min in later snapshots.
- **Conclusion:** these are provider schedule adjustments that track order-of-play progress, not data errors and
  only rarely genuine reschedules. No start time was repaired or imputed.

**The 19 moved events.** The earliest start seen is earlier than the one in the 11:55 discovery snapshot that drove
planning.

| Shift of the frozen anchor | Events |
|---|---|
| −2 to −13 min | 4 |
| −30 to −45 min | 8 |
| −55 to −70 min | 3 |
| −102 to −106 min | 4 |

- For 16 of 19, the earliest estimate appeared in a snapshot observed **before** the planned decision (10:55, 12:25,
  12:55 or 14:25). For 3, it appeared afterwards, including one in-play actual-start stamp.

**Is the frozen anchor (earliest scheduled start ever observed − 60 min) still conservative?** Yes. Checked on all
377 available quote rows:
- source time ≤ observation ≤ decision ≤ every start version − 60 min: **0 violations**;
- no in-play or post-decision quote is used;
- a moved anchor can only make the decision earlier, so it never adds information.

| Effect | Moved (19) | Unmoved (20) |
|---|---|---|
| Events with ≥ 1 available book | 14 | 20 |
| Median available quote age (min) | 17.3 | 10.3 |
| p90 available quote age (min) | 27.9 | 19.5 |

- **Cost:** all 5 unavailable events are moved events, and moved events have staler quotes.
- **Benefit:** using the discovery-time start instead (diagnostic only) would put 4 *other* events' decisions after
  their actual starts as later snapshots show them. Their newest admissible quotes would then be stale.

**Acquisition dependence (new finding, disclosed, protocol unchanged).** The frozen anchor takes the minimum over
*all* stored snapshots. Buying more snapshots of an edition can therefore move decisions earlier after the fact.
1. An edition's population is final only when its acquisition is complete.
2. Stage C (a full Wimbledon 2022 edition) can change Stage A's anchors. The Stage A figures here are provisional for
   the confirmatory run.
3. The confirmatory `pilot-compare` must run only after all acquisition is locked.

## 3. The 5 events without an available quote

| Event | Frozen decision | Cause |
|---|---|---|
| E03 (discovered later) | 10:25 | `no_quote_before_cutoff`: the anchor moved −45 min. The decision precedes the event's first appearance (10:55 snapshot) |
| E10 | 11:45 | `stale`: anchor −55 min; only the 10:55 snapshot precedes the decision (newest quote 54 min old) |
| E20, E21 | 14:00 | `stale`: anchor −36 min; newest quote 39 min old (13:25 snapshot) |
| E28 | 15:14 | `stale`: anchor −106 min; newest quote 33 min old |

- All five are anchor drift under the frozen rule, none a data defect.
- No market was `incomplete_market` at decision.
- The 24 quarantined Stage A quotes affect 2 bookmaker-event pairs before decision. Both events still have 9 other
  available books.

## 4. The 2 Stage A events not linked

| Event | Status | Cause (identity only) |
|---|---|---|
| E08 | `player_unresolved` | The provider lists the full middle-name form of one player's name. Fixed by a curated alias (§5) |
| E17 | `unmatched` | Both names resolve uniquely, but one player does not appear anywhere in the canonical Wimbledon 2022 draw. This is consistent with a listed pairing that never took place (pre-match withdrawal or replacement). **Correctly excluded**, although it has live quotes |

Across all four Slams, the 9 `player_unresolved` cases come from **4 name-format variants**, each matching exactly one
player active in 2021–22:
- a given name written as one word;
- a hyphenated double surname;
- full middle names;
- an apostrophe surname.

The two `duplicate_provider_event` rows (Wimbledon) and the one `ambiguous` row (RG, already in play) are outside
Stage A and correctly excluded.

## 5. Fixes (outcome-blind, with synthetic tests)

1. **Curated aliases** (`src/quantedge/reference/player_name_aliases.csv`): 4 rows, identity only.
   - Re-linking: 9 events change from `player_unresolved` to `linked`. All 179 earlier links are unchanged (same match
     keys).
   - Totals: 188 linked, 2 duplicate, 1 ambiguous, 1 unmatched.
   - Stage A linking goes from 37/39 to **38/39**.
2. **Link scope:** `odds link` now reads canonical matches and tournaments only up to `SplitConfig.inner_end`
   (2022-12-31). Results are identical. The late check and holdout are never read for linking.
3. **Quote-age definitions** (see §7). No change to any threshold or to the gating population.

The owner's `event_links.parquet` was **not** overwritten. Re-running `odds link --stream historical` reproduces the
re-linked figures above.

## 6. Usable-and-linked population (exact intersection)

| Condition (cumulative) | As audited (owner links) | With curated aliases |
|---|---|---|
| Planned Stage A events | 39 | 39 |
| Uniquely linked to a canonical ATP match | 37 | 38 |
| … with a valid complete pre-match two-way h2h market | 37 | 38 |
| … available at the frozen decision with ≤ 30-min source freshness | 32 | 33 |
| … with ≥ 2 independent bookmaker quotes | **32** | **33** |

- Every intersection event has 8–12 available books (median 11), and **at least 7 distinct price sources**.
- 3 of the 4 discovered-later events are in the intersection.
- **Independence caveat:** sport888 and unibet quote identical prices on 34/34 shared events, so they are one price
  source. "≥ 2 independent" is met with large margin regardless. The consensus median counts that source twice; this
  is disclosed, and the protocol is unchanged.

## 7. Quote-age metric (GO check c3)

- **Implementation:** the gate uses the median `staleness_minutes` over each bookmaker's newest admissible complete
  h2h quote, available or stale. That is the population pre-specified in the Stage A preflight §7 before any data.
- **Not a bug:** the code matches its documented definition.
- **Which population is appropriate?** The available-only median is bounded by the 30-min freshness limit and ignores
  the books that failed it. Including stale books can only raise the median. Switching the gate to available-only
  after seeing data would loosen it after the fact. **The gate is therefore unchanged.**
- The two alternative populations are now reported explicitly, without gating, and pinned by synthetic tests.

| Population | Median age (min) | ≤ 20? |
|---|---|---|
| **Gating:** newest admissible quote per book (available ∪ stale), 418 rows | **10.82** | yes |
| Available quotes only (would enter probabilities), 377 rows | 10.57 | yes |
| Event-weighted (median of per-event medians, available) | 10.32 | yes |

The verdict is identical under all three.

## 8. Quote-age distribution and freshness sensitivity

**Available quotes at 30 min:**

| Quantile | 0 | 10 % | 25 % | 50 % | 75 % | 90 % | 99 % | max |
|---|---|---|---|---|---|---|---|---|
| Age (min) | 4.3 | 9.5 | 9.7 | 10.6 | 19.5 | 24.9 | 29.6 | 30.0 |

- **5-min bins:** 0–5: 12; 5–10: 126; 10–15: 121; 15–20: 53; 20–25: 30; 25–30: 35.
- **Components:**
  - market lag (observation − source): median 4.7, p99 5.9, max 14.9 min;
  - decision − observation: median 5, max 25 min (moved anchors are served by older snapshots).

| Freshness | Events ≥ 1 book | Events ≥ 2 books |
|---|---|---|
| 15 min (sensitivity) | 23 / 39 (59 %) | 23 / 39 |
| **30 min (primary)** | **34 / 39 (87 %)** | **34 / 39** |
| 60 min (sensitivity) | 38 / 39 (97 %) | 38 / 39 |

Availability is nearly all-or-nothing per event: books in one snapshot share similar lag, so ≥ 1, ≥ 2 and ≥ 3 coincide.

## 9. Pinnacle and fallback candidates (availability only)

| Population | Pinnacle available | Top books (share) |
|---|---|---|
| 39 planned | 34 (87.2 %) | intertops, pinnacle, sport888, unibet 0.872; mybookieag, onexbet 0.846; betfair 0.795 |
| 33 usable and linked | **33 (100 %)** | intertops, pinnacle, sport888, unibet 1.000; mybookieag, onexbet 0.970 |

- On Stage A, Pinnacle clears the pre-registered 80 % adequacy threshold at decision time. In the single 11:55 probe
  snapshot it was 39/39 structurally.
- **Diagnostic only.** The protocol fixes the reference on the full pilot population at the primary timing.
- AO 2022 showed Pinnacle 0/30 pre-match in the probe, so pooled adequacy remains open.
- If the fallback is ever needed, sport888 (alphabetical tie-break) is a clone of unibet. That is disclosed; the rule
  is not changed.

## 10. Exchanges and commission

**Lay prices.** All 756 `h2h_lay` quotes on Stage A events are market `other` and never selected. The exclusion
continues and remains pinned by `tests/leakage/test_lay_exclusion.py`.

**Exchange back prices (betfair, matchbook).**
- They make up 60 of 368 available pairs (16 %) in the usable population.
- Their booksum is close to 1: median 1.008–1.010, against 1.026 for Pinnacle and 1.04–1.06 for other books.
- Two pairs exceed 1.05 (thin books; max 1.21).
- None is below 1, so the de-vig never refuses.

**Disclosure required:**
1. Exchange back prices are quoted before commission on net winnings; the rate depends on account and jurisdiction
   and is not in the data. They are therefore not like-for-like with bookmaker margins.
2. They may reflect thin liquidity.

For probability benchmarking they are kept as specified (two books within a median consensus; the primary reference
is unaffected). Any execution or profitability analysis must model commission explicitly.

## 11. Stage A go / no-go

The owner-reported audit is reproduced exactly: **GO = true, 7/7 checks.**

| Metric | Value |
|---|---|
| c1, c2: events with ≥ 1 / ≥ 2 books | 87.2 % / 87.2 % |
| c3: gating median age | 10.82 min |
| c4: timestamp violations | 0 |
| c5: linked | 94.9 % as audited, 97.4 % with aliases |
| c6: new quarantine classes | none |
| Spend | 90 ≤ 90 |

**c6 scope note:** quarantine reasons are checked across all stored captures, not only the stage's events. This is a
conservative superset, and the result is unchanged.

## 12. Stage B preflight (offline; not executed)

- **Manifests** were generated with the same rules as Stage A (request at the earliest decision, gap ≤ 15 min,
  duplicates refused). Each one dry-runs with 0 calls and is stored under the gitignored
  `data/processed/odds/the_odds_api/stages/`.
- **Discovery snapshots:** all at 11:55Z on day 2, grid offset :x5.

| Sub-stage | Discovery capture | Requests | Events | Discovered later | Max gap | Credits | Manifest SHA-256 |
|---|---|---|---|---|---|---|---|
| `stage-b-uso-2022` | `cap1_1ed2d772…` | 9 | 48 | 0 | 5 min | 90 | `9d26c49b…9d00` |
| `stage-b-rg-2022` | `cap1_eaadc2ec…` | 10 | 29 | 2 | 10 min | 100 | `0b72917e…9b55` |
| `stage-b-ao-2022` | `cap1_886e5d73…` | 11 | 30 | 0 | 5 min | 110 | `87a7510b…893c` |
| **Total** | | **30** | **107** | 2 | | **300** | |

**Stage B request times (UTC), 10 credits each:**
- **US Open:** 08-30 14:00, 15:00, 16:00, 18:00, 20:00, 21:00, 22:00; 08-31 00:00, 14:00.
- **RG:** 05-24 11:05, 11:34, 12:25, 12:59, 13:30, 13:55, 14:52, 16:32, 17:45; 05-25 08:00.
- **AO:** 01-18 23:00; 01-19 00:15, 01:00, 01:30, 02:30, 03:00, 03:30, 04:30, 04:50, 08:15, 23:00.

**Expected risks, from Stage A:**
1. **Anchor drift.** About half the events may move earlier. AO and US Open requests are sparser in time than
   Wimbledon, so fewer neighbouring snapshots can rescue moved events. Availability may fall below Stage A's 87 %;
   c1 and c2 will measure it.
2. **AO Pinnacle.** It was absent pre-match in the probe. This does not affect the go criteria, but matters for
   reference adequacy.
3. **Linking** depends on the curated aliases (8 of the 9 resolved cases are AO, RG or US Open events).

**Recommended cap:**
- 300 credits planned, **320 absolute** (the ceiling set in the four-Slam plan); the 20-credit contingency is never
  auto-used;
- each sub-stage capped at its own planned total by its own ledger.
- **Expected quota after Stage B:** 19,570.

**Execution order** (each sub-stage needs explicit owner authorisation, then `build-tables`, `link`, `stage-audit`):
US Open → RG → AO. US Open has the highest events-per-credit and AO the Pinnacle risk. **Stop at the first NO-GO.**

## 13. Verdict

**Stage A: GO, confirmed.** Data integrity, spend and point-in-time conservativeness are verified.

**Usable-and-linked population:**
- **32 events** as audited;
- **33** after identity-only alias curation;
- each with ≥ 8 available books and ≥ 7 independent price sources at the frozen decision time;
- Pinnacle available on all of them;
- provisional until acquisition for Wimbledon 2022 is locked (§2).

**Stage B: GO WITH CONDITIONS.**
1. Re-run `odds link --stream historical` with this commit before any Stage B audit. Stage A should then show 38/39
   linked.
2. Execute sub-stages separately (US Open → RG → AO), each capped at its planned total; total ≤ 300 planned, 320
   absolute.
3. Audit after each sub-stage. Report anchor drift (`anchor_moved_unavailable`) and the age populations of §7.
4. Disclose in all reporting: exchange-commission caveats, the sport888 = unibet clone, and acquisition dependence of
   the frozen anchor.
5. No outcome access and no `pilot-compare` until all acquisition is locked.

**Proposed next credit ceiling: 320 (Stage B), 300 planned.** No paid request has been made. Waiting for owner
approval.
