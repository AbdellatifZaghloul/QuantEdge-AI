# Market benchmark v2 — design review (DRAFT, pending owner approval)

Date: 2026-11-02. Status: **design review only. Nothing frozen or activated.**

**Scope:**
- Offline only. **0 network calls, 0 credits.** No acquisition and no `pilot-compare`.
- No outcomes, scores, model metrics or profitability. No 2023–24 or 2025+ rows: canonical identity and calendar data
  come from the inner-fold view only.
- `market-benchmark-v1` unchanged (SHA-256 `e3d3f642…67da21`).
- Raw captures, manifests and ledgers untouched.
- Aggregate figures only.

The proposed protocol text is in `docs/preregistration/market-benchmark-v2-DRAFT.md`. The rules are implemented as
pure functions in `src/quantedge/odds/v2_draft.py`, with synthetic tests in `tests/unit/test_v2_draft.py`.

> **Methodological adaptation (read first).** The 22 stored captures were used to find V1's weaknesses (Stage A and B
> reviews), and the same captures are replayed here to compare V2 candidates.
> - V2 is therefore **not independent** of these data.
> - The replay figures are design diagnostics, not validation.
> - Wimbledon 2022 and US Open 2022 must be treated as **design-exposed editions**. Any confirmatory analysis must
>   report results with and without them.
> - No outcome was looked at, so the adaptation concerns *timing and coverage only*. It cannot have tuned V2 towards a
>   favourable model-vs-market result.
> - One choice coincides with the stored data: the 10:00 local decision time. It is justified a priori (60 min before
>   the standard 11:00 local start of play; owner to verify, §8). It also happens to match stored snapshots on 3 days.
>   This is disclosed, not hidden.

## 1. Why the V1 anchor is acquisition-dependent

Let `S` be the set of acquired snapshots and `c_s(m)` the start shown for match `m` in snapshot `s`.

```
V1:   D_m(S) = min_{s ∈ S, m listed in s} c_s(m) − 60 min
```

1. **Monotone in acquisition.** For S ⊂ S′, D_m(S′) ≤ D_m(S). Every purchase can only move decisions earlier, so the
   population P(S) = {m : a fresh quote exists at D_m(S)} is a function of the purchase history. It is not a property
   of the market.
2. **Self-invalidation.** The snapshot bought to serve D_m often shows a smaller c_s(m). At the US Open that happened
   for 14 of 24 moved events: the purchase moves the decision before the purchase itself.
3. **Hindsight content.** For 9 of 24 US Open events and 6 of 19 Wimbledon events, the anchor-setting version was
   first seen after the decision. That includes in-play "actual start" stamps written after the match began. D_m is
   then defined by information no forecaster had at D_m.
4. **Why buying more snapshots does not fix it.**
   - As S grows to every provider version at 10-min cadence, D_m converges to (earliest version ever, including
     in-play stamps) − 60. That limit is still hindsight-defined (point 3).
   - It costs roughly the price of uniform dense cadence (≈ 5,000 credits per edition, §6), which the budget cannot
     cover.
   - Short of the limit, coverage favours matches whose listed start never moved: session openers.
     - That selection depends on court order and on how long earlier matches lasted. It is outcome-adjacent and
       uncontrolled.
     - At the US Open, the usable V1 set was essentially all session openers (Stage B review §7).

## 2. Candidates

All candidates share:
- the market definitions of V1: h2h, `eu`, newest admissible complete quote per book, source time from `last_update`,
  missing source time rejected;
- identity linking (D-32);
- exclusion of exchange lay prices.

### A1 — Session/day-anchored, data-derived opening

```
K_d  = local(d, 00:00)                                  fixed discovery cutoff
O_d  = min { c_s(m) : s = latest snapshot ≤ K_d, local_day(c_s(m)) = d, c_s(m) > K_d }
T_d  = O_d − 60 min
P_d  = { m listed in S(T_d) with local_day(c_{S(T_d)}(m)) = d and c_{S(T_d)}(m) ≥ T_d }   (first qualification only)
```

It costs 2 snapshots per day (discovery plus decision), or 1 plus an events-endpoint call if that endpoint is used
(unverified, §8).

### A2 — Session/day-anchored, fixed local clock (the "fixed scheduled session snapshot")

```
T_d  = local(d, 10:00) in the tournament time zone      fixed before any data
S_d  = snapshot returned for date = T_d; admissible only if T_d − 10 min < S_d ≤ T_d
P_d  = { m listed in S_d : local_day(c_{S_d}(m)) = d  and  c_{S_d}(m) − T_d ≥ 60 min }   (first qualification only)
```

It needs 1 snapshot per tournament day. Discovery and decision are the same snapshot.

### B — Match-specific point-in-time

```
G    = predetermined poll grid (every 10 min through each play day)
D_m  = min { t ∈ G : m listed in S(t) and 0 < c_{S(t)}(m) − t ≤ 60 min }
```

The decision is fixed at the first poll at which the *then-listed* start is within 60 min. It is never revised.

### Other alternatives considered

| Alternative | Verdict |
|---|---|
| "Last snapshot before actual start" / fixed horizon before actual start | **Rejected:** the actual start is only known in hindsight (in-play stamps); not point-in-time |
| V1 with denser or adaptive purchases | **Rejected:** §1 point 4 (hindsight limit, cost, retroactive population changes) |
| Opening line (first listing) | Point-in-time but days before play; the horizon varies with listing practice; weak estimand |
| Previous-evening snapshot | Point-in-time, but precedes order-of-play publication and overnight withdrawals: the population is less complete |
| A2 plus a second night anchor (local 18:00, for matches listed ≥ 19:00 local) | Kept as an optional **secondary** analysis for AO and US Open night sessions (+≈ 10 credits per night day) |

## 3. Specification comparison

| Item | A1 | **A2 (recommended)** | B |
|---|---|---|---|
| Decision time | O_d − 60 from 00:00-local discovery | Fixed 10:00 local | First 10-min poll within 60 min of the then-listed start |
| Schedule source and as-of time | Provider listing in S(K_d) | Provider listing in S_d (as of T_d) | Provider listing in S(t) |
| Revised or delayed starts | Ignored after K_d / T_d | Ignored after T_d | Ignored after D_m |
| Events not yet listed | Not in P_d; may qualify on a later day if listed for that day | Same | Enter when first listed inside their window |
| Postponed match | Keeps its first decision | Keeps its first decision | Keeps its first decision |
| No quote after actual start | Quotes observed ≤ T_d. **Validity censoring** excludes any m that a later snapshot shows had started by T_d | Same | Same; the risk is higher because the lead can be minutes |
| Model cutoff vs market | Conservative day-bound model cutoff ≤ T_d is required; violations excluded and counted. Replay: 0 violations | Same; replay 0 | Same; replay 0 |
| Freshness | Primary 30 min, sensitivities 15 and 60 (as V1) | Same | Same |
| Identity and duplicates | Point-in-time rule `resolve_identity` (below) | Same | Same |
| Withdrawals or replacements | A listed pairing that does not occur fails to link and is excluded. Replacements listed after T_d are not in P_d | Same | Same |
| Cost per tournament day | 20 (or ≈ 11 with events endpoint) | **10** | ≈ 210 (events-endpoint polls) to ≈ 600 (odds polls) on busy days |

**Point-in-time identity rule (all candidates).**
- A match is decided at its first decision with any in-population claimant.
- Exactly one claimant there: `unique`.
- Two or more concurrent claimants: every one is `identity_conflict`, and the match is excluded for good. A later
  decision can never rescue it.
- Later claims: `already_assigned`.
- A store-wide `duplicate_provider_event` whose twin is not listed at that decision is unique at that moment.
- Nothing is merged, and no claimant is chosen by hindsight.

**Estimand.**
- **A2:** the de-vigged bookmaker probability observed at 10:00 local on the match day, for main-draw matches listed
  for that day at that moment. It is compared with the model's day-bound forecast for the same matches.
  - It is a start-of-day market forecast: horizons run from 60 min to about 11 h before the then-listed start.
  - It is not a "60 minutes before the match" forecast.
- **B:** a match-specific forecast about 50–60 min before the then-listed start.
- **A1:** close to A2, with the anchor at the day's first listed start instead of a fixed clock.

## 4. Replay on the 22 stored captures

**Observed** means a stored snapshot lies inside the candidate's admissible window. **Not observed** means no
snapshot was ever bought there; it does **not** mean no market existed.

| Candidate | Days or polls observed | In population | Unique link | ≥ 2 distinct sources at 15 / 30 / 60 min | Median quote age (min) | Pinnacle |
|---|---|---|---|---|---|---|
| **A2** US Open | 08-30, 08-31 (09-01 not observed) | 48 | 48 | 48 / 48 / 48 | 10.2 (max 11.2) | 31 / 48 |
| **A2** Wimbledon | 06-29 (06-27, 06-28, 06-30 not observed) | 16 | 16 | 16 / 16 / 16 | 10.1 (max 10.9) | 16 / 16 |
| A2 AO, RG | none observed | — | — | — | — | — |
| A1 US Open | 08-31 (08-30 has no discovery snapshot) | 16 | 16 | 16 / 16 / 16 | 10.2 | 0 / 16 |
| A1 Wimbledon | 06-29 | 16 | 16 | 16 / 16 / 16 | 10.1 | 16 / 16 |
| B US Open (stored grid) | 29 of 69 events triggered | 29 | 29 | 29 / 29 / 29 | 5.7 | 29 / 29 |
| B Wimbledon (stored grid) | 22 of 72 triggered; 1 censored (started before its decision) | 21 | 20 | 20 / 20 / 20 | 4.6 | 20 / 20 |
| B RG (stored grid) | 2 of 41 | 2 | 2 | 2 / 2 / 2 | 4.7 | 2 / 2 |

**A2 detail:**
- **Coverage of observed days:** every canonical match whose provider listing at that moment shows it on the day is
  in the population.
  - US Open 08-30: 32 of 32. 08-31: 16 of 22, where the 6 others are provider-duplicate pairings whose surviving ID is
    listed for 09-01 (a not-observed day).
  - Wimbledon 06-29: 16 of 17, where the 1 other is the same kind of duplicate.
- **Clean rules on observed days:** 0 short-lead exclusions, 0 identity conflicts, 0 validity censorings, 0 market
  decisions before the model cutoff.
- **Horizon** (listed start − T_d): US Open 60–660 min (median 300); Wimbledon 60–510 min (median 217).
- **Followed-by matches:** the 24 US Open events lost under V1 are all in A2's population and all available. Their
  start drift after 10:00 is irrelevant by construction.
- **Pinnacle** posts later than other books for evening matches, so its A2 coverage at the US Open is 65 %. The
  pre-registered fallback is likely to apply. Consensus is unaffected (≥ 2 distinct sources on 48/48).

**Wimbledon vs US Open.** Under V1, Wimbledon passed and the US Open failed. Under A2 both reach 100 % on their
observed days. A2 removes the dependence on court-order revisions that drove the difference.

**Sensitivity to missing snapshots:**
- **A2:** each day hangs on one snapshot. If it were missing, the next-older stored snapshot would be 120 min older
  (US Open 08-30), 840 min (08-31) or 970 min (Wimbledon 06-29). The day would be lost, not stale-rescued. In all 22
  paid calls so far the provider returned exactly the expected grid snapshot.
  - With 5-min lag, one or two missing 10-min grid points would still give ages of 15–25 min, within 30. This is
    reasoned, not observed.
- **B** depends on poll density by construction. On the sparse stored grid, 40 of 69 US Open events and 50 of 72
  Wimbledon events never trigger. Those are not observed, not unpriceable.

**Duplicates.**
- Under A2 the duplicate twins resolve naturally: at 10:00 local on a match day, the stale twin is no longer listed.
  Observed in 7 of 7 cases.
- If both twins were listed at T_d, the conservative conflict rule applies.

**Still unknowable or unpriceable:**
- **A2:** none on observed days. Not-observed days account for all remaining gaps (AO, RG, three Wimbledon days, one
  US Open day).
- **B:** in the full-cadence model below, 8 Wimbledon events never trigger, because they are first seen after their
  listed start or in play.

## 5. Hypothetical B at full cadence (a model, not observations)

Schedules are approximated as piecewise-constant between stored versions, on a 10-min grid.

| Day | Events | Distinct trigger snapshots | Poll points over the day |
|---|---|---|---|
| US Open 08-30 | 32 | 15 | 60 |
| US Open 08-31 | 23 | 10 | 56 |
| Wimbledon 06-28 | 31 | 21 | 31 |
| Wimbledon 06-29 | 17 | 9 | 46 |

## 6. Cost for 10 Grand Slam editions

**Editions:** AO 2021–22, RG 2020–22, Wimbledon 2021–22, US Open 2020–22. AO 2020 predates provider coverage;
Wimbledon 2020 was cancelled. That gives about 143 tournament days (RG starts on Sunday). The target is about 1,228
eligible main-draw matches.

| Design | Lower | Central | Conservative | Notes |
|---|---|---|---|---|
| **A2** | 1,350 (≈ 135 days × 10) | **1,580** (143 days plus 10 % contingency, never auto-used) | 3,560–3,900 (adds a night anchor on ≈ 70 AO/US Open nights, plus a pre-registered backup snapshot at T_d − 20 on every day, plus 10 %) | No separate discovery cost |
| A1 | 1,580 (events endpoint at 1 credit, unverified) | 2,860 | 3,560 (plus night anchor) | Discovery needed every day |
| B | ≈ 14,600 (events-endpoint polls at 1 credit, unverified, plus ≈ 90 trigger snapshots per edition) | ≈ 30,000 (midpoint; highly uncertain) | ≈ 65,000 (odds-snapshot polls) | Exceeds or exhausts the 19,780 remaining |
| V1 at robust density (for reference) | — | ≈ 50,000 | — | Uniform 20-min cadence |

**A2 coverage of the 1,228 target** (assumptions, not observations):
- **Central ≈ 90 %** (≈ 1,100 matches). Observed days reached 100 % of then-listed pairings.
- **Lower ≈ 75 %**, allowing for 2020–21 provider gaps, rain-shifted schedules and not-yet-listed pairings.
- **Conservative ≈ 60 %**, if one edition is largely unavailable.
- **Missing-market risks:** Pinnacle adequacy (AO 0/30 in the probe; US Open 65 % at A2), and unknown bookmaker
  coverage in COVID-era editions (RG 2020 in October, AO 2021 in February).

## 7. Recommendation: A2

| Criterion | A2 | A1 | B |
|---|---|---|---|
| Point-in-time validity | Strict: a fixed clock, one as-of snapshot | Strict | Strict, but lead can collapse to minutes; more censoring |
| Real-world information availability | A start-of-day market is what any forecaster could act on | Same | Closest to match time |
| Coverage robustness | Insensitive to court-order revisions; one snapshot per day | Needs two snapshots per day | Needs dense polling; sparse grids fail |
| Cost | **≈ 1,430–1,580** | ≈ 2,860 | ≥ 14,600 |
| Simplicity and auditability | Highest | Medium | Low |

**Cost of choosing A2:**
- The estimand moves from "≈ 1 h before each match" to "start of match day".
- Later matches carry longer horizons, so the market has less late information. That is a disclosed property, not a
  bias towards any result.
- The market still has at least as much information as the day-bound model. The replay found 0 violations.

## 8. Unresolved assumptions (owner to confirm before freezing)

1. **10:00 local:** this assumes standard play starts at 11:00 local at all four Slams in 2020–22. Verify from official
   sources. The short-lead rule excludes any match listed < 60 min after T_d, so an early start is safe but costs
   coverage.
2. **Tournament days and time zones:** RG starts on Sunday, the others on Monday, with finals on the second Sunday.
   Wimbledon 2021 had a rest day on the middle Sunday (that request returns no listings). Time zones are fixed per
   tournament; DST is handled by `zoneinfo`.
3. **Provider grid:** a 10-min cadence before Sept 2022, with an :x5 offset observed in 2022. This is unverified for
   2020–21; the runner stops on any snapshot outside (T_d − 10, T_d].
4. **Historical availability:** coverage for 2020–21 Slams and the `eu` bookmaker set is unknown.
5. **Historical events endpoint:** cost and availability are unverified; only A1 and B depend on it.
6. **Night sessions:** the single anchor gives evening matches 8–11 h horizons. The night anchor is secondary and
   optional.
7. **Identity:** linking requires that the canonical match occurred. The population is therefore conditional on the
   listed pairing being played. This is unavoidable and identical across candidates.
8. **Clones and exchanges:** sport888 = unibet, so distinct sources are counted on distinct price pairs. Exchange
   back prices are pre-commission (Stage A results §10).
9. **Gate-0 eligibility:** applied only at confirmatory time; not computed here, so no outcome-adjacent fields were
   touched.
10. **Design exposure:** Wimbledon 2022 and US Open 2022 (see the adaptation note at the top).

## 9. Prospective-style acquisition algorithm (A2)

1. **Freeze inputs before any purchase:** editions, time zones, the 10:00 local rule and the day-range rule. Day ranges
   come from the canonical calendar (start date, nominal days), with no schedule data. The result is one request per
   tournament day.
2. **Build immutable manifests** with `day_anchor_manifest`. Each manifest is ≤ 130 credits (so editions are split by
   week) and its SHA-256 is recorded. Each request carries a decision sentinel.
3. **Execute** with `odds stage-run --execute --approve-paid-calls` after explicit approval of that manifest:
   - cumulative append-only ledger capped at the planned total;
   - `max_retries = 0`;
   - quota reconciled per call;
   - **stop** if the returned snapshot is outside (T_d − 10, T_d].
4. **No rescue and no re-timing.** A day whose snapshot is missing or late is recorded as missing. No other time is
   requested. A non-charged transport error may be retried once, after owner review.
5. **Build, link and resolve offline:** `build-tables`, `link`, then `day_anchor_population`, `apply_validity_censoring`
   and `resolve_identity`.
6. **Lock each edition:** record a file of the capture hashes used. Nothing further is acquired for a locked edition.
   The confirmatory run happens only after every edition is locked.

## 10. Smallest useful next experiment (requires owner approval; not executed)

| Item | Proposal |
|---|---|
| Scope | **US Open 2021, week 1** (08-30 … 09-05): an edition never inspected. Covers about 116 of 127 main-draw matches (rounds 1–3 and part of round 4), night sessions included |
| Requests | 7 × 10 = **70 credits** at 14:00 UTC (10:00 EDT) |
| Manifest | A2 manifest `v2-feasibility-uso-2021-wk1`, generated offline (SHA-256 `137ef604…dfc3b`); dry run: 7 × `would_request`, 0 calls, no ledger |
| Ceiling | 70 planned = 70 absolute; contingency 0 |
| Outcome-blind go criteria, fixed now | (1) ≥ 6 / 7 snapshots inside (T_d − 10, T_d]; (2) ≥ 90 % of week-1 canonical main-draw matches (identity only) in the population on their first qualifying day; (3) ≥ 85 % of the population with ≥ 2 distinct sources at 30 min; (4) gating median age ≤ 20 and available median ≤ 15; (5) 0 decisions before the model cutoff and ≤ 1 % validity censoring; (6) ≤ 2 % identity conflicts |
| Stop rules | Stop at the first runner stop. Stop after day 2 if either snapshot lists no main-draw events |
| What it tests | Whether A2's coverage holds on unseen data, including night sessions, before any wider spend |

Week 2 (about 11 matches) would cost another 70 credits; it is not proposed until week 1 passes.

## 11. Verdict

1. **Preferred protocol: A2.** It is the only design with strict point-in-time validity that is also insensitive to
   schedule revisions and affordable. Its decision time is fixed in advance; schedule information is taken as listed
   at that moment; identity is resolved point-in-time; later information is used only to exclude.
2. **Data and budget can support a defensible benchmark** under A2: about 1,430–1,580 credits for 10 editions against
   19,780 remaining. The estimand is a start-of-day market forecast. Availability for 2020–21 is the main unknown.
3. **The 10-edition pilot is realistic under A2,** and unrealistic under V1 or B.
4. **Smallest useful next experiment:** US Open 2021 week 1, **70 credits**, with the fixed go criteria above. It
   requires explicit owner approval *and* the owner freezing V2, or at least its decision and identity rules, first.
5. **Further paid acquisition** should proceed only in that order: freeze V2, approve the 70-credit feasibility, audit,
   then decide on the editions. Nothing proceeds automatically. **No credits were spent.**
