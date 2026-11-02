# Market benchmark v2 — design review (DRAFT, pending owner approval)

Date: 2026-11-02 (readiness review appended in §12). Status: **design review only. Nothing frozen or activated.**

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
Wimbledon 2020 was cancelled. That gives **143 tournament days** exactly: rest days are requested too, and the count was rechecked against the draft calendar in §12 (RG starts on Sunday). The target is about 1,228
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
| Scope | **US Open 2021, week 1** (08-30 … 09-05): an edition never inspected, night sessions included. Canonical day bounds put **96** main-draw matches certainly inside the window (R128 and R64) and up to **124** possibly inside. This corrects an earlier rough figure of 116 |
| Requests | 7 × 10 = **70 credits** at 14:00 UTC (10:00 EDT) |
| Manifest | `v2a2-feasibility-uso-2021-wk1`, generated offline by `odds v2-manifest` from the frozen-calendar draft (SHA-256 `df880fd4…aceb2`, strict snapshot window). Dry run: 7 × `would_request`, sentinel events counted as 0, 0 calls, no ledger. The earlier `v2-feasibility-uso-2021-wk1` (`137ef604…`) is superseded and is refused by the runner |
| Ceiling | 70 planned = 70 absolute; contingency 0 |
| Outcome-blind go criteria, fixed now | (1) ≥ 6 / 7 snapshots inside (T_d − 10, T_d]; (2) ≥ 90 % of the 96 canonical main-draw matches certainly inside the window (identity only) are `unique` at their first qualifying decision; the share of the up-to-124 possible matches is reported; (3) ≥ 85 % of the population with ≥ 2 distinct sources at 30 min; (4) gating median age ≤ 20 and available median ≤ 15; (5) 0 decisions before the model cutoff and ≤ 1 % validity censoring; (6) ≤ 2 % identity conflicts |
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

## 12. Readiness review (2026-11-02, offline, outcome-blind)

Scope: 0 network calls, 0 credits; no acquisition, no outcomes, no model comparisons, no late-check or holdout rows.
V1 (`e3d3f642…67da21`), the raw store, all existing manifests and both ledgers are byte-identical before and after.

### 12.1 Production integration issues found and fixed

| # | Issue | Fix | Tests |
|---|---|---|---|
| 1 | **Identity schema mismatch.** Production `event_links` is keyed by `event_id` with column `status`; the draft expected `provider_event_id` and `link_status`. The replay had renamed columns ad hoc | `identity_links()` adapter: validates required columns, unique `event_id` and `provider_event_id`, no orphan links, and a `match_key` on every linked or duplicate row; otherwise it fails loudly | 5 cases |
| 2 | **Silent drop in de-vig.** `market_probabilities` keeps only `status == "linked"`, so events V2 resolves as unique despite a store-wide duplicate would vanish | `links_for_market()` passes exactly the V2-`unique` events, as `linked` | end-to-end audit |
| 3 | **Identity concurrency too narrow.** Only in-population twins counted, so a twin listed concurrently for another day was missed | Claimants are now *all* claimable provider events listed in the decision's as-of snapshot; conflicts at the first decision exclude the match for good | concurrency case |
| 4 | **Calendar inferred from purchased data.** `_days()` derived days from snapshots | Replaced by a frozen reference calendar (`reference/v2_draft_edition_calendar.csv`, `load_calendar`). Days outside it are refused; rest and empty days are explicit rows | calendar, rest-day, subset cases |
| 5 | **Boundary mismatch.** The draft says `T_d − 10 < S ≤ T_d`, but the runner accepted `T_d − 10:00` exactly | `StageManifest.strict_snapshot_window` (default false, so V1 manifests are unchanged). Day-anchor manifests set it and the runner stops at age ≥ 10 min | T−9:59 pass, T−10:00, T−10:01 and T+0:01 stop |
| 6 | **Sentinels counted as events.** The ledger's `events_planned` and the report's `events` included the scheduling sentinel; `stage_audit` would have used V1 denominators | Sentinels are excluded from all counts; `stage_audit` refuses day-anchor manifests; `plan_stage` refuses sentinel-prefixed IDs; legacy sentinel manifests without the strict window are refused | 3 cases |
| 7 | **No acquisition lock.** | `write_lock` (write-once: manifest and ledger hashes, paid captures); `run_stage` refuses a locked stage; CLI `odds v2-lock` | lock case |
| 8 | **No V2 audit.** | `v2_audit` and CLI `odds v2-audit`. The denominator chain is: canonical main-draw matches certainly or possibly in the audited days → listed in population → unique → fresh ≥ 1 book → ≥ 2 distinct sources → de-vig usable. It also reports reasons, sensitivity, both age populations, Pinnacle and horizon. Model-cutoff violations are excluded | end-to-end synthetic audit, CLI |

**Restart safety (verified by test).** After a stop on day 2 (an old snapshot), a re-run skips days 1–2 and requests
day 3 only. The ledger stays cumulative (10/20/30), there is no duplicate request, and day 2 remains
`snapshot_missing`: it is not re-timed or rescued.

**Real-schema smoke run** (design-exposed 2022 days; pipeline check, not validation): the end-to-end audit reproduces
the replay.
- US Open 08-30/31: 48 in population, 48 unique, 48 with ≥ 2 distinct sources, 48 de-vig usable.
- Wimbledon 06-29: 16 / 16 / 16 / 16.
- For 1–2-day windows the canonical day bounds are too wide, so the "certainly in days" denominator is 0. Partial
  windows must therefore be reported as a [certain, possible] range.

### 12.2 Start-of-play assumption (10:00 local is before normal main-draw play)

- **Primary sources:** official dated orders of play were **not consulted**; this review is offline. That is a
  condition for freezing (§12.5).
- **Local evidence** from stored 2022 provider listings, schedule fields only: on every day whose schedule was listed
  before play began, the earliest listed start was **11:00 local** at all four Slams.
  - AO 2022: 01-19, 01-20.
  - RG 2022: 05-25.
  - Wimbledon 2022: 06-29, 06-30.
  - US Open 2022: 08-30, 08-31, 09-01.
  - Days first listed mid-day are uninformative.
- **2020–2021:** no local evidence.
- **Safety if the assumption fails:** a match listed < 60 min after `T_d` is `short_lead` and excluded, and actual
  starts before `T_d` are censored. An earlier start of play can cost coverage but cannot breach point-in-time
  validity. Neither rule was weakened.

### 12.3 Coverage and cost recheck

- **Provider coverage** (`reference/odds_api_tennis_coverage.csv`, documented in DATA_SOURCES §4.2): historical data
  for US Open from 2020-08-28, RG 2020-09-24, AO 2021-02-06 and Wimbledon 2021-06-25. All 10 editions start after
  their sport's coverage date, so **US Open 2021 is covered**. Coverage dates say nothing about which bookmakers quote.
- **Days and credits:**
  - AO 14 + 14, RG 15 + 15 + 15, Wimbledon 14 + 14, US Open 14 + 14 + 14 = **143 requests = 1,430 credits**.
  - The Wimbledon 2021 rest day and the expected no-ATP-singles Saturdays are still requested (fixed calendar).
  - The 10 % contingency (≈ 143) is never auto-used.
- **Coverage scenarios** (90 / 75 / 60 %) are **forecasts, not measurements**.
- **Feasibility:** 7 requests, **70 credits planned = 70 absolute**.

### 12.4 Phases of evidence (no conflation)

| Phase | Data | Status | Claims allowed |
|---|---|---|---|
| V1 → V2 estimand change | — | V1 anchor ≈ 60 min before each match (acquisition-dependent); V2 = start-of-match-day market | A different estimand, not a fix of V1 |
| Exploratory design | Wimbledon 2022, US Open 2022 (design-exposed) | Done | Design diagnostics only; never independent validation |
| Acquisition feasibility | US Open 2021 week 1 (unseen) | Proposed; needs approval | Coverage, timing and identity only; no outcomes |
| Confirmatory comparison | All locked editions | After V2 freeze, all locks, and separate approval | Model vs market under the frozen V2, once; frozen Phase 2 predictions; 2023–24 and 2025+ untouched |

The V2 timing rule will not be revised in response to model outcomes.

### 12.5 Readiness verdict: PASS WITH CONDITIONS (for freezing V2; not for spending)

**Engineering:** pass. The draft rules run end to end on production schemas, all integration gaps above are fixed and
tested, and full static checks and tests pass.

**Conditions before freezing** (owner, offline):
1. Verify each edition's first day, last day and rest days (e.g. whether Wimbledon 2022's middle Sunday was a play
   day) against official sources. Record them in the calendar with `verified = true` and a source note.
2. Verify from official dated orders of play that 10:00 local precedes normal main-draw play on every day of the 10
   editions, recording any exceptions. Exceptions do not change the rule; they are reported as expected `short_lead`
   coverage loss.
3. Set the programme credit ceiling. The design suggestion is 1,430 planned plus 143 contingency (never auto-used).
4. Approve the freeze, creating a new protocol ID and SHA-256 and recording it in DECISIONS.

**Only after freezing and a separate approval of 70 credits**, the commands would be:

```
uv run quantedge odds stage-run --name v2a2-feasibility-uso-2021-wk1            # dry run: 7 × would_request
uv run quantedge odds stage-run --name v2a2-feasibility-uso-2021-wk1 --execute --approve-paid-calls
uv run quantedge odds build-tables --stream historical
uv run quantedge odds link --stream historical
uv run quantedge odds v2-audit --edition uso-2021 --stages v2a2-feasibility-uso-2021-wk1
uv run quantedge odds v2-lock --name v2a2-feasibility-uso-2021-wk1                # only after the audit is reported
```

## 13. Freeze prerequisites (2026-11-02)

The full package, with source links, is in `docs/preregistration/market-benchmark-v2-freeze-checklist.md`. The
candidate protocol is `docs/preregistration/market-benchmark-v2-CANDIDATE.yaml`: NOT FROZEN, and pinned to the code
and calendar by a test. Public web pages were read only for calendar, start-of-play and pricing evidence. There were
no odds-provider calls and no credits.

- **Calendar:**
  - 10 / 10 main-draw date ranges match dated secondary sources.
  - Rest days are sourced for Wimbledon 2021 (Middle Sunday off) and Wimbledon 2022 (Middle Sunday play); the draft
    calendar was already right on both.
  - The other rest days and all expected-empty days are assumptions.
  - **0 / 10** editions are fully verified from official sources, and every row stays `verified = false`.
- **Start of play:**
  - 11:00 local is supported on sample days: primary or tour-official for RG 2020, RG 2022, Wimbledon 2021 and US
    Open 2020; secondary for AO 2021; weak for AO 2022 and US Open 2021; only 2022 provider listings otherwise.
  - It is not established for every day.
  - Validity is protected regardless: listings < 60 min after `T_d` are `short_lead`, and earlier actual starts are
    censored.
- **Charging:** the provider documents 10 per region per market and no charge for responses with empty data. The
  budget conservatively assumes all 143 requests are charged: **1,430 base**, plus a separate 143 contingency (1,573
  maximum, never automatic). A truly empty snapshot (charged 0) stops the runner for review.
- **Execution safety gap closed:** the US Open 2021 feasibility now runs as **tranche 1 (2 requests, ≤ 20 credits)**,
  then an offline gate (`v2-tranche-check`), then **tranche 2 (5 requests, ≤ 50 more)**.
  - Tranche 2 needs a separate approval and a passing, write-once, ledger-bound gate.
  - Whole-manifest runs are refused, and any stop blocks all tranches until owner review.
  - Request times are unchanged.
- **Partial audit:** `v2-audit` audits only the requested days and lists the rest as pending. No complete edition or
  lock is required.

## 14. Organizer evidence incorporated (2026-11-03)

Details and exact quotations are in `docs/preregistration/market-benchmark-v2-freeze-checklist.md` §1–§3.

- **First and last main-draw days:** **9 / 10** organizer-stated; Wimbledon 2022 organizer-derived (Middle Sunday
  3 July plus the 14-day format from 2022); none changed.
- **Unretrievable reviewer links:** `ausopen.com/tickets-ao22` (HTTP 403) and the three JavaScript-rendered usopen.org
  articles. Other organizer pages (ausopen.com, usta.com, a usopen.org PDF) were used instead and cited.
- **Rest days:** Wimbledon 2021 and 2022 supported (organizer).
- **Assumptions under the reporting-only waiver:** rest days for the other editions, and every expected empty day.
- **Start of play:** primary for RG 2020–2022 and Wimbledon 2021–2022; tour-official for US Open 2020. It is still
  not established for every day. The 10:00 local anchor and 60-minute minimum listed lead are unchanged.
- **Budget:** programme ceiling 1,430 (feasibility 70 included); contingency 143 only by separate approval.
- **Unchanged and re-verified:** the two-tranche implementation and its files.
- **Disclosure:** one search snippet incidentally showed a single 2022 Wimbledon match result; it was not used.
