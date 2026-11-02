# DRAFT — `market-benchmark-v2` (NOT FROZEN, NOT ACTIVE)

> **Status: proposal for owner review.**
> - No protocol ID has been registered and no SHA-256 exists.
> - No code path loads this file, and `market-benchmark-v1` stays frozen and unchanged.
> - Freezing requires an explicit owner decision. A YAML file would be created at that point with a new fingerprint
>   and a `docs/DECISIONS.md` entry, **before** any outcome is accessed.
>
> Design rationale and replay: `docs/reviews/market-benchmark-v2-design-review.md`.

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

- **Days:** the first main-draw day (Sunday for RG, Monday otherwise) through the final Sunday. The range comes from
  the canonical calendar only.
- **As-of snapshot S_d:** the provider snapshot returned for `date = T_d`. It is admissible only if
  `T_d − 10 min < S_d ≤ T_d`. Otherwise day `d` is `snapshot_missing`: it is recorded, never re-timed and never
  rescued.
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
2. A canonical match is decided at its first decision with any in-population claimant:
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

- **Denominators, at every step:**
  1. canonical eligible main-draw matches;
  2. listed at their first qualifying `T_d`;
  3. `unique`;
  4. available at 30 min;
  5. ≥ 2 distinct sources.
- **Exclusions are reported by reason:** `snapshot_missing`, `no_listing`, `short_lead`, `started_before_decision`,
  `identity_conflict`, `not_linked`, `stale`, `incomplete_market`.
- **Also reported:** horizon distribution (listed start − `T_d`); quote-age quantiles (gating population and
  available-only); bookmaker availability; and coverage by round, by day and by edition.
- Absent snapshots are never interpreted as absent markets.

## Acquisition (fixed before purchase)

- **Manifests:** one request per tournament day at `T_d`, built by `day_anchor_manifest`. Each manifest is ≤ 130
  credits (weekly split), and its SHA-256 is recorded before any call.
- **Runner:** `odds stage-run` after explicit owner approval per manifest. It keeps a cumulative ledger capped at the
  planned total, uses `max_retries = 0`, reconciles the quota per call, and stops on any snapshot outside
  `(T_d − 10, T_d]`.
- **No other purchases:** no rescue, no adaptive re-timing, no additional snapshots for an edition after it is locked
  (capture hashes recorded).
- **Confirmatory run:** only after all editions are locked.
- **Budget guard:** the programme ceiling is set by the owner at freeze time. The design central estimate is about
  1,580 credits, including a 10 % contingency that is never auto-used.

## Optional secondary analysis (decided at freeze time)

A night anchor at local 18:00 for matches listed, at that time, to start ≥ 19:00 local (AO and US Open). It costs
about 10 credits per night-session day, is reported separately and is never primary.

## Metrics and conduct

These are as in V1: paired log-loss and Brier differences, block bootstrap by ISO week, and one confirmatory run.
They are not computed in the design phase.
