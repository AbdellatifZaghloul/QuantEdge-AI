# Phase 5 — LightGBM candidate vs frozen Elo (inner development folds)

Date: 2026-11-03. Run `d10ff67cd3b7` (config `configs/experiments/phase5_gbm.yaml`, model version `phase5-gbm-v1`).

**Scope:**
- Odds data is not touched: **0 Odds API calls, 0 credits**, no acquisition, no V2 odds, no `pilot-compare`.
- **Every reader is cut at `splits.inner_end` (2022-12-31)** through `phase5.load_inner_tables`, which has a test. The
  2023–24 late check and the 2025+ holdout were never loaded.
- Frozen V1/V2, the Phase 2 Elo run and all fingerprints are unchanged.
- Research data (CC BY-NC-SA); non-commercial research only. No betting, profitability or market claim.

## 1. What was built

- **Features** (`src/quantedge/models/gbm_features.py`): **27 point-in-time features**, 19 antisymmetric A − B
  differences plus 8 shared context features.
  - **Ratings:** overall and surface Elo (the Phase 2 replay, with the pre-registered reference config fivethirtyeight
    k_c 250); Elo difference, mean, and match-count uncertainty.
  - **Ranks:** point-in-time ranks (Phase 2 helper, 365-day staleness, cap 2000) with an unranked indicator.
  - **Serve/return statistics:** shrunk serve-points-won, return-points-won and break-points-saved, decayed with a
    365-day half-life, plus surface versions shrunk towards the player's overall.
  - **Form:** Elo-residual form (90-day half-life).
  - **Workload:** matches and minutes in the last 14/30 days, days since last match, an inactivity flag, and rounds
    already into the draw.
  - **Other:** age difference; best-of, level, surface, draw category and round.
- **Point-in-time rules:**
  - statistics, form and workload come from a **knowability-only sweep**: a result counts only once
    `knowable_at_utc ≤ prediction_cutoff_utc`, so statistics never use the same-tournament earlier-round exception;
  - Elo and ranks reuse the frozen Phase 2 machinery.
- **Model** (`src/quantedge/models/gbm.py`): LightGBM 4.7.0, deterministic, fixed seed. Per fold:
  1. select among **4 pre-declared configurations** by early-stopped log-loss on the last fit-window year (y − 2),
     training on years ≤ y − 3;
  2. refit on the whole fit window;
  3. slope-only recalibration on y − 1;
  4. test on y.
  - Predictions are symmetrized, `p = (f(x) + 1 − f(swap x)) / 2`, so `p(A, B) = 1 − p(B, A)` exactly.
- **Evaluation:** on **exactly** the frozen Phase 2 inner test matches (run `0a5b056019ec`). The runner refuses
  population or label mismatches. Paired differences use the ISO-week block bootstrap (2,000 replicates, 450 blocks).

## 2. Population and missingness

| Item | Value |
|---|---|
| Eligible inner test matches (2013–2022), identical for both models | **101,587** |
| Eligible development rows with features (2005–2022) | 174,956 |
| Usable features | **27** (19 A − B, 8 shared) |
| Missing values | only `age_diff`: 0.1 % of development rows, 0 % of test rows (LightGBM handles NaN natively) |
| Imputed by design (not missing) | rank absent or stale: 1.5 % of test player-slots (capped at 2000 plus indicator); no knowable statistics: shrunk to the point-in-time tour mean |
| Matches 2005–2022 carrying serve/return statistics | 142,468 of 181,966 (78 %) |
| Exclusions | Gate-0 eligibility (`eligibility.v1`), as in Phase 2 |

## 3. Results (inner folds, pooled 2013–2022)

| Model | Log-loss | Brier | Accuracy | ECE | Calibration slope |
|---|---|---|---|---|---|
| **LightGBM, recalibrated** | **0.59272** | **0.20425** | 0.6778 | 0.0033 | 0.995 |
| LightGBM, raw | 0.59280 | 0.20428 | 0.6778 | 0.0035 | 0.993 |
| Elo overall, recalibrated (frozen baseline) | 0.61322 | 0.21286 | 0.6576 | 0.0032 | 0.990 |
| Elo surface, recalibrated (frozen) | 0.61356 | 0.21301 | 0.6581 | 0.0033 | 0.988 |

**Paired differences, LightGBM_recal − Elo, with 95 % block-bootstrap CIs:**

| Contrast | Estimate | 95 % CI |
|---|---|---|
| Log-loss vs Elo overall (**primary**) | **−0.02050** | **[−0.02202, −0.01900]** |
| Log-loss vs Elo surface | −0.02084 | [−0.02235, −0.01930] |
| Brier vs Elo overall | −0.00861 | [−0.00925, −0.00799] |
| Brier vs Elo surface | −0.00876 | [−0.00940, −0.00813] |

**By test year** (log-loss LightGBM / Elo; LightGBM ECE and calibration slope):

| Year | n | LightGBM | Elo | Difference | ECE | Slope |
|---|---|---|---|---|---|---|
| 2013 | 9,017 | 0.5718 | 0.5888 | −0.0170 | 0.012 | 0.95 |
| 2014 | 8,744 | 0.5694 | 0.5887 | −0.0193 | 0.011 | 1.02 |
| 2015 | 9,509 | 0.5783 | 0.5953 | −0.0169 | 0.012 | 0.97 |
| 2016 | 12,272 | 0.5733 | 0.6091 | −0.0358 | 0.019 | 1.10 |
| 2017 | 11,556 | 0.5883 | 0.6096 | −0.0213 | 0.011 | 0.96 |
| 2018 | 12,549 | 0.6012 | 0.6227 | −0.0215 | 0.011 | 0.98 |
| 2019 | 11,074 | 0.6167 | 0.6317 | −0.0150 | 0.014 | 0.95 |
| 2020 | 4,595 | 0.6182 | 0.6383 | −0.0201 | 0.018 | 0.94 |
| 2021 | 9,847 | 0.6127 | 0.6280 | −0.0153 | 0.014 | 1.00 |
| 2022 | 12,424 | 0.6034 | 0.6223 | −0.0189 | 0.015 | 1.04 |

- **Stability:** improvement in every year, with no degradation.
- **Calibration:** per-year slopes 0.94–1.10; recalibration slopes fitted on y − 1 were 0.91–1.09.
- **Selection:** the chosen configurations vary by fold (all 4 were used), with 141–520 rounds.

## 4. Where the gain comes from (descriptive diagnostics; not used for the decision)

| Ablation (same folds and protocol) | Log-loss | vs frozen Elo | Full model vs this |
|---|---|---|---|
| Elo-derived features only (differences, mean, uncertainty) | 0.6041 | −0.0091 [−0.0102, −0.0081] | −0.0114 |
| Everything except the new serve/return, form and workload statistics | 0.5937 | −0.0195 [−0.0210, −0.0180] | **−0.0010 [−0.0014, −0.0005]** |

- **Main drivers:** nonlinear use of Elo with its uncertainty, ranks, and match context (level, draw, best-of).
- **The new statistics add little:** a small but significant −0.001.
- **Importance (mean normalized gain):** Elo difference 0.52, rank difference 0.13, surface Elo 0.13, minimum Elo match
  count 0.035, days since last match 0.024, age 0.024.
- **Largest gains** come where plain Elo is weakest or ignores the format:

| Level / draw | Matches | Difference vs Elo |
|---|---|---|
| Challenger qualifying | 14,800 | −0.056 |
| Davis Cup | 2,200 | −0.046 |
| Tour qualifying | 7,321 | −0.027 |
| Grand Slam main draw (best-of-5) | 4,741 | −0.022 |
| Challenger main draw | 46,989 | −0.012 |
| Masters main draw | 4,994 | −0.008 |

## 5. Leakage, determinism and integrity checks

| Check | Result |
|---|---|
| Own result knowable before own cutoff | **0 / 377,590**; the earliest own knowability is 170 h after cutoff (median 218 h) |
| Future-perturbation (synthetic): results knowable after the cutoff, including the shrinkage prior, change nothing | pass |
| Test-year labels flipped: identical predictions and selection | pass |
| Calibration-year labels flipped: only the slope changes; selection identical | pass |
| Exact antisymmetry `p + p(swap) = 1` | pass (1e-12) |
| Determinism: same-seed retraining on real data | **bit-identical** on all 101,587 predictions |
| Inner-end reader: no rows after 2022-12-31 | pass |
| Decision-rule code: KEEP only if the CI upper bound < 0 | pass (synthetic) |

**Procedure note.** The first execution crashed at the end, in the decision step (a wrong CI key name), before any
metric was printed or registered. The key was fixed, with a regression test, and the identical pre-declared
configuration was re-run once. No result was seen before that rerun, and nothing was tuned on test years.

## 6. Decision

- **Statistically better than Elo on the inner folds: yes.** Log-loss −0.0205 [−0.0220, −0.0190] and Brier
  −0.0086 [−0.0093, −0.0080], with improvement in all 10 test years and comparable calibration.
- **Verdict under the pre-declared rule: KEEP**, as a *development* result.
- **Caveats:**
  - The inner test years were already scored for Elo in Phase 2, and the feature design is not blind to the
    literature. This is not confirmation.
  - Confirmation needs a separately approved, single counted late-check look of the frozen `phase5-gbm-v1`. It has
    **not** been taken.
  - The 2016 gain (−0.036) is unusually large and should be inspected before any confirmatory look.
- **A calibrated ensemble is not the priority.** The model already contains Elo as its dominant input, so a
  GBM + Elo ensemble is likely to add little.
- **Better next experiments** (each pre-declared, still inner-fold only):
  1. a parsimonious variant without the new statistics, whose gain is nearly identical (simpler, less overfitting
     risk);
  2. a per-level breakdown with format-aware Elo as a stronger baseline;
  3. only then, an owner decision on one late-check look.
