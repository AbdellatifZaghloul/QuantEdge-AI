# Phase 5 scientific review before any late check (EXPLORATORY)

Date: 2026-11-03.
- Reviewed: commit `b55bfe4`, run `d10ff67cd3b7` (`phase5-gbm-v1`).
- Exploratory run: `c54432527c98` (config `configs/experiments/phase5_review.yaml`).

**Scope:**
- 0 Odds API calls and 0 credits; no acquisition and no V2 outcome comparison.
- Every reader is cut at `splits.inner_end` (2022-12-31): **no 2023–24 late-check or 2025+ holdout data was read**.
  The late-check look counter for `phase5-gbm-v1` is **0**.
- `phase5-gbm-v1` (config, code and run) is unchanged. Frozen protocols, captures, ledgers and locks are untouched.

> **Disclosure.** The 2013–2022 inner test folds have now been inspected several times (Phase 2, `phase5-gbm-v1`, this
> review). Every comparison below is **exploratory**. None is confirmation, and none may be used to change
> `phase5-gbm-v1`.

## 1. The 2016 improvement: a data-coverage onset, not a defect

**Challenger qualifying draws enter the archive in 2016.** Eligible test matches per year: 2015 **39**, 2016
**3,275**, 2017 2,882, 2018 3,517, 2019 256, 2020 379, 2021 1,591, 2022 2,861. Tour qualifying volume also halves from
2016 (about 1,150 → about 610 per year).

| Segment | Year: n, LightGBM − Elo log-loss (Elo log-loss) |
|---|---|
| Challenger qualifying | 2016: 3,275, **−0.104** (0.636); 2017: −0.057 (0.610); 2018: −0.041; 2021: −0.025; 2022: −0.041 |
| Challenger main | −0.006 to −0.016 in every year (2016: −0.010) |
| Tour qualifying | 2013–15: about −0.04; 2016 onwards: about −0.015 |
| Grand Slam main | −0.014 to −0.035 every year (2016: −0.022) |

- **Attribution:** challenger qualifying contributes **−0.028** of 2016's −0.036. Without it, 2016 is ordinary.
- **Mechanism:** these players had little or no Elo history when the segment appeared, so Elo was close to 50 / 50
  (log-loss 0.636). LightGBM separates them through point-in-time ranks and Elo match-count uncertainty.
- **Not leakage:** the 2016 fold was trained without this segment (only 39 such matches before 2016), and the gain
  decays as Elo histories accumulate. That is the expected signature of cold-start information, not of leakage.
- **No anomaly:** no duplicate, label or timestamp anomaly was found (own-result knowability ≥ 170 h after cutoff,
  Phase 5 review §5).

## 2. Point-in-time audit of every feature

| Feature group | Source and timing | Finding |
|---|---|---|
| Elo, surface Elo, Elo counts, days since last match | Phase 2 replay (knowability rule plus earlier-round outcome exception) | **Valid** (Phase 2-tested) |
| Ranks, unranked indicator | `rankings` with `knowable_at ≤ cutoff`, 365-day staleness | **Valid** |
| Serve/return, break points saved, surface versions, stats volume | Knowability-only sweep; tour prior from knowable rows only | **Valid** (future-perturbation tests, including the prior) |
| Elo-residual form | Past results' own pre-match Elo probability; knowable only | **Valid** |
| Workload (14/30-day matches and minutes) | Knowable results with event time before cutoff | **Valid**; conservative, since same-week results are not yet knowable |
| Age | `match_entry.age_at_event_start` (birth date) | **Valid** |
| Level, surface, best-of, draw category, round | Pre-match tournament and match metadata | **Valid** |
| Category codes (`_codes`) | Sorted labels of the loaded data | **Valid** (labels only, no outcome). Robustness note: the mapping is data-derived, so a frozen late-check run should pin it |
| `rounds_into_draw` | `round_ordinal − min(round_ordinal)` over the draw's **complete records** | **Not strictly point-in-time** (derived from complete tournament records), but empirically structural (below) |

**`rounds_into_draw` in detail:**
- **Agreement with the structural first round:** the minimum recorded round equals the first round implied by the
  pre-tournament `draw_size` in all ordinary draws except about 28 out of about 3,900. The 1,336 other disagreements
  are Davis Cup and Finals round-robin or team-tie labelling. Qualifying draws start at Q1 in 2,009 of 2,015 cases.
- **Contribution:** dropping the feature changes log-loss by only **+0.00027 [+0.00007, +0.00047]** overall, and by
  nothing significant on Slams or outside challenger qualifying.
- **Classification:** a design weakness, not outcome leakage. The value never depends on who won.

**Evaluation population.** Gate-0 eligibility excludes walkovers and retirements. This is the same population
definition as Phase 2, identical for both models. Prospective use would face those matches, so any later market or
paper-trading analysis must state this population.

## 3. Format-aware Elo diagnostic (`phase5-elo-format-diag-v1`)

- **Method:** reference Elo (identical to the frozen Elo here: 0.61322 in both). For best-of-5 matches, the implied
  set probability is solved from the best-of-3 match probability and converted to a best-of-5 match probability.
  Then slope-only recalibration on y − 1, as in Phase 2. No new fitted parameter.
- **Tests:** synthetic, covering exact inversion, antisymmetry and test-year label independence.

| Comparison (a − b), 95 % ISO-week block CIs | All (101,587) | Grand Slam main (4,741) |
|---|---|---|
| Format Elo − frozen Elo, log-loss | −0.00041 [−0.00058, −0.00025] | **−0.00745 [−0.00994, −0.00503]** |
| Format Elo − frozen Elo, Brier | −0.00012 [−0.00018, −0.00007] | −0.00231 [−0.00311, −0.00150] |
| LightGBM v1 − format Elo, log-loss | −0.02009 [−0.02160, −0.01859] | **−0.01475 [−0.02004, −0.00975]** |
| LightGBM v1 − format Elo, Brier | −0.00849 [−0.00911, −0.00787] | −0.00594 [−0.00795, −0.00405] |

- **Elo on Slams:** frozen Elo is **under-confident** on Grand Slam main draws (calibration slope 1.35, ECE 0.048).
  The format transform fixes most of this (slope 1.07, ECE 0.017).
- **LightGBM's Slam gain is not only format:** it remains −0.0148 better than format-aware Elo there.
- **LightGBM is also somewhat under-confident on Slams** (slope 1.12, ECE 0.019) under its pooled slope-only
  calibration. This is a calibration limitation, not a defect.

## 4. Simpler variant (`phase5-gbm-lite-exp1`, exploratory)

This is v1's code and settings with the 11 new serve/return, form and workload features removed. v1 is not replaced or
retuned.

| | All | Grand Slam main | Excluding challenger qualifying |
|---|---|---|---|
| Lite log-loss / v1 log-loss | 0.59368 / 0.59272 | 0.49614 / 0.49724 | 0.59901 / 0.59793 |
| Lite − v1, log-loss | +0.00096 [+0.00054, +0.00136] | −0.00111 [−0.00326, +0.00101] | +0.00108 [+0.00064, +0.00152] |
| Lite − v1, Brier | +0.00043 [+0.00025, +0.00061] | −0.00040 [−0.00127, +0.00048] | +0.00047 [+0.00028, +0.00066] |
| Lite − frozen Elo, log-loss | −0.01954 [−0.02108, −0.01805] | −0.02331 [−0.02901, −0.01726] | −0.01332 [−0.01472, −0.01201] |

- **Overall:** the new statistics add a small, significant improvement (about 0.001).
- **On Slams:** the difference is not significant.
- **Calibration:** both are well calibrated overall (ECE 0.004, slope 0.99).
- **Robustness:** excluding the challenger-qualifying cold-start segment, v1 still beats frozen Elo by −0.0144 (point
  estimate; v1 − format Elo there is −0.0139 [−0.0153, −0.0125]), so the gain is not driven by that segment.

**Per-year log-loss (exploratory):**

| Year | Frozen Elo | Format Elo | v1 | Lite |
|---|---|---|---|---|
| 2013 | 0.5888 | 0.5882 | 0.5718 | 0.5725 |
| 2014 | 0.5887 | 0.5883 | 0.5694 | 0.5684 |
| 2015 | 0.5953 | 0.5945 | 0.5783 | 0.5792 |
| 2016 | 0.6091 | 0.6088 | 0.5733 | 0.5735 |
| 2017 | 0.6096 | 0.6096 | 0.5883 | 0.5888 |
| 2018 | 0.6227 | 0.6225 | 0.6012 | 0.6026 |
| 2019 | 0.6317 | 0.6312 | 0.6167 | 0.6178 |
| 2020 | 0.6383 | 0.6376 | 0.6182 | 0.6201 |
| 2021 | 0.6280 | 0.6276 | 0.6127 | 0.6155 |
| 2022 | 0.6223 | 0.6218 | 0.6034 | 0.6048 |

## 5. Readiness for one separately authorized 2023–24 late check

**No actual defect** in `phase5-gbm-v1` requires a fix first:
- no point-in-time leak was found;
- `rounds_into_draw` is a minor design weakness with a negligible effect;
- the 2016 effect is explained by data coverage;
- results are bit-identical on retraining.

**Conditions before any late-check look** (none of them done):
1. **Freeze `phase5-gbm-v1` exactly as run:**
   - config SHA-256;
   - code fingerprint and commit `b55bfe4` for `models/gbm.py`, `models/gbm_features.py`, `experiments/phase5.py`;
   - LightGBM 4.7.0;
   - the category-code mapping pinned from the inner run.
2. **Build and review a separate late-check runner.** The current runner is inner-fold-only by design. The new runner
   must:
   - reuse v1's code unchanged;
   - use the Phase 2 late-check folds (fit to y − 2, calibrate y − 1, test y ∈ {2023, 2024});
   - record the look through `record_late_check_look("phase5-gbm-v1", …)` (cap 3);
   - compare against the already-registered Phase 2 late-check Elo on identical matches;
   - be tested on synthetic data only before use.
3. **Pre-declare the late-check decision rule before the look**, e.g. primary pooled paired log-loss
   (v1 − frozen Elo) on 2023–24, confirmed if the 95 % ISO-week block CI upper bound < 0.
4. **Disclose in any report:** Phase 2 Elo has already used 2 of 3 late-check looks, so the 2023–24 Elo results are
   known. The late check is therefore blind only for LightGBM.

## 6. Assessment

| Item | Result |
|---|---|
| 2016 anomaly | **PASS**: explained (challenger-qualifying coverage onset); no data anomaly |
| Point-in-time validity of all features | **PASS**, with one documented weakness (`rounds_into_draw` from complete records; effect +0.0003) |
| Format-aware Elo diagnostic | Done (exploratory): fixes Elo's Slam under-confidence; LightGBM still better |
| Simpler variant | Done (exploratory): the new statistics add about 0.001 overall |
| **`phase5-gbm-v1` ready to be frozen for one authorized late check** | **PASS**: no defect requires a fix. Execution is **not yet possible** until the reviewed late-check runner and pre-declared rule exist (§5) |

**No late check was performed. No profitability or market claim is made.**
