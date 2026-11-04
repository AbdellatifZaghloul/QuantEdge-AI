# V2 model-vs-market evaluation engine: offline preparation

Date: 2026-11-04. **Offline only:** 0 Odds API calls, 0 credits, no acquisition and no outcome-based
model-vs-market comparison. No 2025+ holdout access. `phase5-gbm-v1` was not rerun or retrained, and its late check
was not reopened (1 look used). V1 (`e3d3f642…da21`), V2 (`ad319ba7…e70c`), the calendar, `v2_draft.py` and `stage.py`
are unchanged: the fingerprint tests pass and the integrity baseline is 43/43.

## 1. What exists and what is new

The frozen V2 code produces the population (`day_anchor_population`, `apply_validity_censoring`), identity
(`resolve_identity`, `links_for_market`) and an outcome-blind coverage audit (`v2_audit`). **It has no scoring step.**
The frozen protocol also declares no bootstrap seed, model columns or contrasts (only V1 does).

New, separately versioned:
- `src/quantedge/evaluation/market_v2.py`, engine `model-vs-market-v2-engine.v1`;
- `configs/preregistration/model_vs_market_v2_engine.yaml`, status **DRAFT**. It pins the V2 SHA and declares the
  model source (`phase5-gbm-v1` walk-forward inner-fold predictions for 2020–2022, run `d10ff67cd3b7`), the contrasts
  and the bootstrap seed 20261104. It must be frozen by the owner before any scoring.

| Stage | Behaviour (outcome-blind unless stated) |
|---|---|
| `eligible_population` | Applies the frozen V2 statuses unchanged, in order: `status` ≠ `in_population`, `identity` ≠ `unique`, started before the decision, not Gate-0 eligible, market decision before the model's prediction cutoff. Every exclusion is counted. A match claimed twice is refused |
| `admissible_quotes` | A quote must be observed in (T_d − 10 min, T_d], have a source time ≤ T_d, be fresh (≤ 30 min, the frozen primary) and have valid prices (> 1). A missing source time is rejected. Every rejection has a reason. The latest admissible quote per bookmaker is kept |
| `devig_quotes` | Existing `odds.devig`: proportional (primary); power and Shin (secondary) |
| `choose_reference` | Pinnacle if its availability is ≥ 0.80; otherwise the highest-availability book, with identical price pairs (clones) collapsed. Outcome-blind |
| `market_benchmarks` | Reference-book probability, plus the median consensus of distinct sources (≥ 2) |
| `align` | Exactly one model probability and one market row per eligible match. Duplicates are refused; missing model, reference or consensus values are counted |
| `price_report` | Overround quantiles, overround by book, best quoted prices across books, matches where best prices underround, and a 0–3 % cost grid. Always `executable: false`, with reasons |
| `outcome_blind_fingerprint` | SHA-256 of the aligned population, recorded before outcomes are joined |
| `score` (outcome-dependent) | Requires `approve_outcome_access=True`. Refuses outcome columns in its inputs, any holdout date, duplicate outcomes and missing outcomes. Computes paired log-loss and Brier (model − market) for the reference and the consensus, with ISO-week block bootstrap CIs |
| `oriented_quotes` | Adapter from the frozen selector output to engine quotes, oriented to player A exactly as `odds.baseline` does |

**Three layers stay separate:**
1. **Probability quality.** The paired scores; the only decision-relevant output.
2. **Theoretical market benchmark.** De-vigged reference and consensus. These are not prices anyone could bet.
3. **Executable prices.** **Unavailable.** Historical snapshots carry no stake limits, liquidity, account
   restrictions, latency or slippage. Only margins, best quoted prices and a cost grid are reported.

## 2. Validation (offline, synthetic)

`tests/unit/test_market_v2_engine.py`: **12 passed**. The tests cover:
- the frozen statuses and exclusion counts;
- rejection of quotes that are after the decision, outside the window, stale, missing a source time or invalid;
- **adding post-decision quotes (even at 3× the price) leaves every benchmark unchanged;**
- de-vig, reference selection, fallback with clone collapse, and consensus requiring ≥ 2 distinct sources;
- outcome columns refused at every outcome-blind step;
- scoring refused without approval, refused on holdout dates, refused with missing outcomes;
- **randomising or flipping outcomes leaves the aligned population's fingerprint unchanged;**
- the paired Brier score checked exactly against a hand computation;
- the price report is never executable;
- duplicate claims and duplicate predictions refused;
- adapter orientation.

| Check | Result |
|---|---|
| Full pytest | **456 passed** |
| CI selection | **444 passed**, 12 deselected |
| Ruff, mypy (69 files), hygiene | Pass |
| AT-19 offline rebuild | Pass, 0 network attempts |
| Integrity baseline | 43/43 |

## 3. Critical limitation: overlap with inspected years

V2 covers Slam editions in **2020–2022**. These are inner development years that were inspected repeatedly
(Phase 2, `phase5-gbm-v1`, the Phase 5 review). The model's 2020–2022 probabilities are genuine walk-forward
predictions (fit to y − 2, calibrated on y − 1), but model design choices were made while looking at results in those
years. Therefore:
- a V2 comparison is **not an independent confirmation** of LightGBM against the market;
- it is **not evidence of profitability**: it measures probability quality against a de-vigged, non-executable
  benchmark, on a narrow and liquid segment (Slam main draws);
- Wimbledon 2022 and US Open 2022 are also design-exposed in the V2 design; report with and without them;
- the only independent market test would be prospective (forward-captured odds) or on the sealed 2025+ holdout, and
  either needs a separate protocol and approval.

## 4. Readiness for further V2 acquisition

**Verdict: READY for the next separately approved tranche (US Open 2021 week 2, 70 credits).** It is **NOT READY** for
any outcome-based comparison until the blockers below are cleared.

**Credit budget** (local ledgers, read offline through `v2_guard.programme_state`):
- spent **70** (US Open 2021 week 1: locked, `lock.json` present);
- remaining **1,360** of the frozen 1,430 ceiling, for the 136 remaining planned days, at 10 credits per day;
- **zero slack**; the contingency is not authorized;
- a `v2a2-uso-2021-wk2` manifest exists, with 0 spent.

**Blockers:**
1. **Owner approval** for each paid tranche: none is given by this document.
2. **Freezing the engine config** (`model_vs_market_v2_engine.yaml`, currently DRAFT), owner-approved, before any
   outcome is joined.
3. **A V2 model-comparison wiring command** (population → engine → `score`), with a one-run counter mirroring
   `conduct.max_confirmatory_runs: 1`. It is not built yet, so no comparison is possible by accident.
4. **Model predictions for the V2 population** must come from the frozen run's 2020–2022 test folds. Coverage of the V2
   matches is unknown until they are aligned (outcome-blind).
5. **Pinnacle coverage risk:** it is absent from the feed from 1 September 2021 (62/114 in week 1). The frozen fallback
   rule will probably apply, and that is reported.
6. **Zero budget slack:** any failed or partial day cannot be retried (`max_retries: 0`).

No API call, purchase or bet was made, and no profitability is claimed.
