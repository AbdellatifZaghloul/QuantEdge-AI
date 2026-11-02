# `market-benchmark-v2` — design prose (superseded by the FROZEN protocol)

> **FROZEN 2026-11-03:** `configs/preregistration/market_benchmark_v2.yaml` (SHA-256
> `ad319ba7384cd95d5b1c2c45fd5c3c058af6af41c6e95c3926f253e990fae70c`, DECISIONS D-40) is the authoritative
> protocol. This file is the pre-freeze draft prose, kept for the record. Where it differs from the frozen YAML,
> the YAML governs; for example, the 143 contingency is not authorized.

## Pre-freeze draft text (historical)

> **Status: proposal for owner review.**
> - No protocol ID has been registered and no SHA-256 exists.
> - No code path loads this file, and `market-benchmark-v1` stays frozen and unchanged.
> - Freezing requires an explicit owner decision. A YAML file would be created at that point with a new fingerprint
>   and a `docs/DECISIONS.md` entry, **before** any outcome is accessed.
>
> Design rationale and replay: `docs/reviews/market-benchmark-v2-design-review.md`. Candidate YAML:
> `docs/preregistration/market-benchmark-v2-CANDIDATE.yaml`. Owner checklist:
> `docs/preregistration/market-benchmark-v2-freeze-checklist.md`.

## Question

How does the probability quality of the frozen Phase 2 models compare with de-vigged bookmaker probabilities
**observed at the start of each match day**, on identical ATP Grand Slam main-draw matches? This is a
probability-quality comparison, not a trading or profitability test.

## Population

| Item | Rule |
|---|---|
| Provider, market, region | The Odds API, h2h, `eu`. Exchange lay prices excluded |
| Editions | Grand Slam main draws 2020–2022 with provider coverage: AO 2021–22, RG 2020–22, Wimbledon 2021–22, US Open 2020–22 |
| Canonical requirements | Level G, main draw. Gate-0 evaluation eligibility (`eligibility.v1`), applied at confirmatory time only |
| Design-exposed editions | Wimbledon 2022 and US Open 2022. Results are reported with and without them |

## Decision time (candidate A2)

For each tournament day `d`, in the tournament's local time zone (Australia/Melbourne, Europe/Paris, Europe/London,
America/New_York):

```
T_d = local(d, 10:00)
```

- **Days:** every day of the edition in the frozen calendar `reference/v2_draft_edition_calendar.csv`: first
  main-draw day (Sunday for RG, Monday otherwise) through the final Sunday. That is 143 days for the 10 editions.
  - Rest days and expected no-ATP-singles days are requested too, and reported as `rest_day` or
    `expected_no_atp_singles` rather than skipped.
  - The calendar is fixed before any purchase and never derived from returned data. Days outside it are refused.
  - **Date verification vs complete-calendar verification.** First and last days are organizer-sourced for all 10
    editions (`dates_verified`). Rest-day and expected-empty columns are
    reporting-only labels under a documentary waiver: every day is requested regardless. They stay marked as
    assumptions, so complete-calendar `verified` is `false` for every row.
- **As-of snapshot S_d:** the provider snapshot returned for `date = T_d`. It is admissible only if
  `T_d − 10 min < S_d ≤ T_d`: strict lower bound, inclusive upper bound. The runner enforces exactly this window
  (`strict_snapshot_window`) and stops at age ≥ 10 min or on any snapshot after `T_d`. Otherwise day `d` is
  `snapshot_missing`: it is recorded, never re-timed and never rescued.
- **Population P_d:** every provider event listed in `S_d` whose start **as listed in `S_d`** falls on local day `d`
  and is at least 60 min after `T_d`.
  - Listed starts < 60 min after `T_d` are `short_lead` and excluded.
  - Events listed for other days belong to those days.
  - An event or match enters at its **first** qualifying decision only. A postponed match keeps that decision.
- **Later schedule revisions** never change `T_d` or `P_d`. They are used only for **validity censoring**: a decision
  is excluded as `started_before_decision` if any snapshot shows the event's start at or before `T_d`.
- **Not-yet-listed events** at `T_d` are not in `P_d`. They can qualify only if listed for a later day in that day's
  snapshot.

## Identity (point-in-time)

1. Link provider events to canonical matches by identity only (D-32 scope: inner folds; curated aliases).
2. **Claimants** of a canonical match at a decision are *all* provider events listed in that decision's as-of snapshot,
   for any day, whose production link status is `linked` or `duplicate_provider_event` with that `match_key`.
   Production `event_links` is adapted by `identity_links`, which fails loudly on schema or uniqueness violations.
   A match is decided at its first decision with any in-population claimant:
   - one claimant: `unique`;
   - ≥ 2 concurrent claimants: `identity_conflict`, and the match is excluded permanently;
   - later claims: `already_assigned`.
3. Nothing is merged and no claimant is chosen. A listed pairing that does not occur (withdrawal or replacement) fails
   to link and is excluded. The population is conditional on the listed pairing being played.

## Market observation

- **Selector rule:** each bookmaker's newest admissible complete two-way h2h quote observed at or before `T_d`, with
  source time from market `last_update`.
  - Missing source time: rejected.
  - Blocking flags: as in V1 (`source_after_observation`, `observed_after_commence`, `snapshot_after_request`,
    quarantined prices).
- **Freshness:** primary 30 min; sensitivities 15 and 60 min.
- **Distinct sources:** books quoting identical price pairs in the same observation count as one source (e.g.
  sport888 and unibet).
- **References:**
  - primary: Pinnacle if available on ≥ 80 % of the linked population, else the outcome-blind highest-availability
    book (alphabetical tie-break; clones collapsed);
  - secondary: median consensus over ≥ 2 distinct sources;
  - de-vig: proportional primary, power and Shin secondary (as V1).

## Information asymmetry

- The model uses the conservative day-bound cutoff (`prediction_cutoff_utc`). Any match with `T_d` before that cutoff
  is excluded and counted; the replay found none.
- The market therefore has at least as much information as the model, and later matches of a day carry multi-hour
  horizons.
- **Estimand:** a start-of-match-day market forecast, not a ~60-minute pre-match forecast.

## Reporting and missing data

- **Denominators, at every step** (`odds v2-audit`, never V1's `stage-audit`):
  1. canonical main-draw matches. For partial-edition windows, report the range [certainly inside, possibly inside]
     from canonical day bounds. Gate-0 eligibility is applied only at confirmatory time;
  2. listed at their first qualifying `T_d`;
  3. `unique`;
  4. available at 30 min;
  5. ≥ 2 distinct sources;
  6. de-vig usable (V2-`unique` events passed to the market step as linked, so none is silently dropped).
- **Exclusions are reported by reason:** `snapshot_missing`, `no_listing`, `short_lead`, `started_before_decision`,
  `identity_conflict`, `not_linked`, `stale`, `incomplete_market`.
- **Also reported:** horizon distribution (listed start − `T_d`); quote-age quantiles (gating population and
  available-only); bookmaker availability; and coverage by round, by day and by edition.
- Absent snapshots are never interpreted as absent markets.

## Acquisition (fixed before purchase)

- **Manifests:** one request per tournament day at `T_d`, built by `odds v2-manifest` (`day_anchor_manifest`) from the
  frozen calendar. Each manifest is ≤ 130 credits (weekly split), is write-once, and its SHA-256 is recorded before any
  call.
  - Each request carries one internal **sentinel** holding `T_d`. Sentinels are scheduling controls only: they are
    never counted as events or availability.
  - Legacy sentinel manifests without the strict window are refused.
- **Runner:** `odds stage-run` after explicit owner approval per manifest. It keeps a cumulative ledger capped at the
  planned total, uses `max_retries = 0`, reconciles the quota per call, and stops on any snapshot outside
  `(T_d − 10, T_d]`.
- **No other purchases:** no rescue and no adaptive re-timing.
  - A restart skips paid requests (ledger and store) and continues with the next planned day only.
  - `odds v2-lock` writes a write-once lock (manifest and ledger hashes, paid captures). The runner refuses any
    further request for a locked stage.
- **Feasibility run in tranches:** US Open 2021 week 1 runs as tranche 1 (2 requests, ≤ 20 credits), then an
  offline gate, then tranche 2 (5 requests, ≤ 50 more). Tranche 2 needs a separate approval and a passing,
  write-once, ledger-bound gate. A whole-manifest run is refused, and any stop blocks all tranches until owner review
  (`v2-tranche-plan`, `v2-tranche-run`, `v2-tranche-check`).
- **Charging:** 10 credits per request (h2h × eu); responses with empty data are not charged by the provider. The
  budget assumes every request is charged (1,430 base). A charge mismatch stops the runner.
- **Confirmatory run:** only after all editions are locked.
- **Budget guard:** the programme ceiling is set by the owner at freeze time. The design central estimate is about
  1,580 credits, including a 10 % contingency that is never auto-used.

## Optional secondary analysis (decided at freeze time)

A night anchor at local 18:00 for matches listed, at that time, to start ≥ 19:00 local (AO and US Open). It costs
about 10 credits per night-session day, is reported separately and is never primary.

## Phases of evidence

1. **Exploratory, design-exposed:** Wimbledon 2022 and US Open 2022. They are never independent validation.
2. **Acquisition feasibility:** US Open 2021 week 1, 70 credits, on separate approval. It measures coverage, timing
   and identity only.
3. **Confirmatory:** a single model-vs-market run after the freeze and after every edition is locked. It uses frozen
   Phase 2 predictions; the 2023–24 late check and 2025+ holdout stay untouched.

The timing rule is not revised in response to model outcomes.

## Metrics and conduct

These are as in V1: paired log-loss and Brier differences, block bootstrap by ISO week, and one confirmatory run.
They are not computed in the design phase.
