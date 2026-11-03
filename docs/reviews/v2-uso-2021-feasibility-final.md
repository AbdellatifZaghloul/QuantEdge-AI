# V2-A2 feasibility — US Open 2021, week 1: final outcome-blind review

Date: 2026-11-03. Protocol: `market-benchmark-v2`, frozen (SHA-256 `ad319ba7…fae70c`, calendar `2db8ee7b…27d4a1`).

**Scope:**
- Offline only. **0 network calls and 0 additional credits** in this review.
- No outcomes, scores, profitability or model comparisons.
- Canonical data comes from the inner-fold view (≤ 2022); there is no late-check or holdout access.
- Identity and round labels only: draw position is used, never a result.
- No lock was written. The raw store, the manifests, both earlier ledgers, the feasibility ledger, the tranche plan and
  the tranche-1 gate are byte-identical before and after (41 files re-hashed).
- Aggregate figures only.

**What this establishes, and what it does not:**
- It establishes acquisition feasibility and coverage for 7 days of one unseen edition.
- It does **not** establish coverage of the full edition (week 2 was not acquired), and it says nothing about model
  quality.

## 1. Acquisition integrity (verified)

| Check | Result |
|---|---|
| Captures | 29 / 29 bodies re-verified against SHA-256 (22 earlier + 7 new); 29 distinct |
| Manifest / tranche plan | `df880fd4…aceb2` / `605a7eea…1411`, unchanged |
| Ledger | 7 / 7 entries, same IDs and request dates as the manifest; 10 credits each; quota 19,780 → **19,710** in exact steps; cumulative **70** = planned |
| Strict as-of timing | Every snapshot returned at **T_d − 5 min** (13:55Z for 14:00Z), inside `(T_d − 10 min, T_d]` |
| Source timestamps | Every market `last_update` ≤ its snapshot |
| Tranches | Tranche-1 gate `pass`, bound to the ledger's first 2 lines (prefix hash re-verified); no stop markers; no lock |
| Credentials | No API key in any stored parameter |

## 2. Independent reproduction of the seven-day audit

Odds tables rebuilt in memory are identical to the on-disk build: 337 events, 20,574 raw quotes, 20,503 valid, 71
quarantined, all `price_not_above_one`. Links rebuilt in memory from the inner-fold view have identical values (only
column order differs from the CLI file). The V2 audit reproduces the owner's figures exactly:

| Item | Value |
|---|---|
| Days observed / pending | 7 / 0 |
| Provider events in population | 116 (0 `short_lead`, 0 censored) |
| Unique canonical matches | 114 (2 `not_linked`, 0 identity conflicts, 0 decisions before the model cutoff) |
| ≥ 1 fresh book / ≥ 2 distinct sources at 30 min | 114 / 114 |
| De-vig usable | 114 |
| Freshness sensitivity, 15 and 60 min | 114 / 114 at both |
| Gating quote age (min): min, median, p90, max | 9.22, **10.12**, 11.08, 1,449.87 |
| Available quote age (min): min, median, p90, max | 9.22, **10.10**, 11.05, **14.27** |
| Horizon (listed start − decision, min) | 60 to 690 (median 210) |
| Canonical main draw: certainly / possibly inside the 7 days | **32 / 124** |

Per day (in population / unique main-draw): 08-30 32/31, 08-31 32/31, 09-01 16/16, 09-02 16/16, 09-03 8/8,
09-04 8/8, 09-05 4/4.

## 3. The 96-versus-32 discrepancy: a planning-estimate error, not a code defect

- **The rule.** The canonical rule (`reference/round_day_bounds.csv`, Slam rows) gives:
  - **R128: −1 to +4 days** from the canonical start date ("First round may start the Sunday before tourney_date");
  - **R64: 0 to +6**.
- **Applied to US Open 2021** (start 30 Aug): R128 runs 29 Aug – 3 Sep; R64 runs 30 Aug – 5 Sep.
- **What the audit counts.** "Certainly inside" means `match_day_lo ≥ first day` and `match_day_hi ≤ last day`.
  R128's lower bound (29 Aug) is before the window, so **only R64 qualifies: 32 matches**.
- **Where 96 came from.** The readiness review (§12) counted R128 + R64 as certainly inside, overlooking the −1-day
  lower bound. The audit code is correct and matches its documented definition. The 96 was a planning-estimate error
  in the documentation.
- **124 possible** = R128 64 + R64 32 + R32 16 + R16 8 + QF 4; each range overlaps the window.
- **Not a defect.** The −1 bound is a deliberate, conservative Phase 1 rule. The frozen organizer calendar does put the
  first main-draw day at 30 Aug, so R128 matches in fact fall within 30 Aug – 3 Sep. But the audit's "certain"
  definition uses canonical bounds only, by design, and is not changed.

## 4. Pre-registered feasibility criteria (design review §10, as amended in §12 before acquisition)

**Criterion 2's denominator.** The text says "the 96 canonical main-draw matches certainly inside the window" and §10
identifies them as "R128 and R64". That wording contains the planning error, so the criterion is evaluated under
**both** readings, and neither is substituted silently:
- **Reading A, the named set (R128 + R64 = 96):** 94 / 96 unique at their first qualifying decision = **97.9 %**.
- **Reading B, the bound definition ("certainly inside" = 32):** 32 / 32 = **100 %**.
- **Reported share of possible matches:** 114 / 124 = 91.9 %.

| # | Criterion | Observed | Result |
|---|---|---|---|
| 1 | ≥ 6 / 7 snapshots inside `(T_d − 10, T_d]` | 7 / 7 | **PASS** |
| 2 | ≥ 90 % of the certainly-inside canonical matches `unique` | 97.9 % (A) and 100 % (B) | **PASS** under both readings |
| 3 | ≥ 85 % of the population with ≥ 2 distinct sources at 30 min | 114 / 116 = 98.3 % (114 / 114 of unique) | **PASS** |
| 4 | Gating median age ≤ 20 min; available median ≤ 15 min | 10.12; 10.10 | **PASS** |
| 5 | 0 decisions before the model cutoff; validity censoring ≤ 1 % | 0; 0 / 116 | **PASS** |
| 6 | Identity conflicts ≤ 2 % | 0 / 116 | **PASS** |
| — | Stop rules: either of days 1–2 without a unique main-draw listing; any runner stop | 31 and 31; no stops | not triggered |

**Not determinable from these data** (and not claimed):
- coverage of the full edition, since rounds R16 (4 of 8 in window), QF, SF and F fall in the unacquired week 2;
- any statement about model or market quality.

The 114 / 116 listing ratio measures identity resolution within the observed listings. It is not canonical coverage.

## 5. Identity (identity-only evidence; rules unchanged)

- **The two `not_linked` population events** (08-30 and 08-31). Both names resolve uniquely, but in each pair one
  player does not appear in the canonical 2021 main draw.
  - One is `unmatched`: there is no canonical match for the pair.
  - The other is `time_inconsistent`: the pair's only canonical match is in a different tournament.
  - This is consistent with listed pairings that did not take place as listed (withdrawal or replacement). They are
    **correctly excluded**, and they account for the two R128 canonical matches not covered (62 / 64).
- **Duplicates.** The store-wide count rose from 16 to 24 rows, i.e. **4 new duplicate canonical matches**, all US Open
  2021.
  - In each, two provider IDs for the same pair were **never co-listed**: sequential re-IDs listed for the same local
    day.
  - Exactly one claimant was in the V2 population at the first decision, so under the frozen point-in-time rule all
    4 are `unique` and retained. A store-wide exclusion (the V1-style linker status) would have lost them.
  - The 7 US Open 2022 duplicates remain outside this population.
- **The single store-wide `time_inconsistent` row** is the population event above. The store-wide `unmatched` rows are
  this event and the earlier Wimbledon 2022 one.

## 6. Bookmaker availability (reference rule unchanged)

| Day | 08-30 | 08-31 | 09-01 | 09-02 | 09-03 | 09-04 | 09-05 |
|---|---|---|---|---|---|---|---|
| Pinnacle available / unique matches | 31/31 | 31/31 | 0/16 | 0/16 | 0/8 | 0/8 | 0/4 |
| Pinnacle in the raw snapshot (events) | 64/64 | 32/48 | 0/32 | 0/24 | 0/16 | 0/12 | 0/8 |

- **Pinnacle** is absent from the provider's `eu` feed from 1 Sep onward. This is a provider coverage pattern, not a
  pipeline effect. Overall it covers 62 / 114 = 54 %, below the pre-registered 80 % adequacy threshold on this sample.
- **Other books:** mybookieag, sport888 and unibet cover 114 / 114 (sport888 and unibet are clones); betclic and
  onexbet 111; marathonbet 107; matchbook 100; intertops 97; betfair 94; williamhill 88.
- **Reference choice:** made only on the full pilot population by the frozen rule. It is diagnostic here.

## 7. Quote ages

- **The 1,449.87-min maximum gating age** is intertops' newest admissible quote for some 31 Aug events: observed in
  the **30 Aug** 13:55 snapshot, because intertops did not quote those events in the 31 Aug snapshot.
  - It is correctly `stale`.
  - There are 19 stale bookmaker rows out of 1,131 (17 intertops, 2 onexbet).
- **No stale quote is in the usable population.** The maximum available age is 14.27 min (all ≤ 30), and the gating
  median (10.12) includes the stale rows, as pre-specified.

## 8. Verdict and recommendations

**Feasibility: GO.**
- All six pre-registered criteria pass. Criterion 2 passes under both readings of its flawed denominator.
- No stop rule triggered.
- Spend was exactly 70 / 70.

**What is not established:** full-edition coverage and the Pinnacle reference adequacy, which falls back by the frozen
rule if it is still below 80 % on the full population.

**Lock:** recommended, after owner review of this report.
- `odds v2-lock --name v2a2-feasibility-uso-2021-wk1` writes the write-once lock: manifest and ledger hashes, 7
  captures.
- After that, nothing more is acquired for this stage. US Open 2021 week 2 would be a separate stage and manifest.

**Further editions:** scientifically justified, but each needs explicit approval. Nothing is initiated.
- **Remaining programme:** 1,430 − 70 = **1,360** credits for the remaining **136** planned days. That is exactly one
  request per day, **with zero slack**; the contingency is not authorized.
- **Suggested order:**
  1. US Open 2021 week 2 (7 days, 70 credits), to complete this edition and allow a full-edition coverage check;
  2. the remaining editions, one weekly manifest (≤ 130 credits) at a time, each audited before the next.
