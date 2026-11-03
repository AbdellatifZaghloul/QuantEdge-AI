# `phase5-gbm-v1` freeze and late-check preparation (PREPARATION ONLY)

Date: 2026-11-03.

**Scope:**
- 0 Odds API calls and 0 credits; no acquisition; no bookmaker or V2 outcome comparison.
- **No 2023–24 late-check data and no 2025+ holdout data were read.** The new runner was **not executed**, not even in
  dry-run mode against the real `runs/`. It was exercised only on synthetic data in `tmp_path`.
- No LightGBM training, tuning or retraining happened on real data. The freeze record was built from development data
  cut at `inner_end` (2022-12-31), by encoding labels only.
- `runs/late_check_looks.json` has **no `phase5-gbm-v1` entry (0 looks)**; `phase2-elo-v1` still has 2.
- V1, V2, the calendar, `stage.py`, `v2_draft.py`, the frozen Elo runs and the `phase5-gbm-v1` run are unchanged. The
  43-file integrity baseline matches both before and after (43/43).

## 1. Freeze manifest (`configs/frozen/phase5_gbm_v1.json`)

The freeze record's SHA-256 is `5887fae1431e6d2b63134e05dfddd38d2b301bc21bed0cee8bba0e6ee8e2e1c5`. It was written once by
`quantedge experiment phase5-freeze`, which refuses to overwrite an existing record.

| Item | Frozen value |
|---|---|
| Nature | A frozen **procedure**. Per-fold models are refit with the same code, settings and rules on each fold's own window |
| Source commit | `b55bfe4bffa03fde7213e27560a39d079b9155ad` |
| Frozen files | 13 files, each verified byte-for-byte (CRLF-normalised SHA-256) against `git show b55bfe4:<file>`. They are `models/gbm.py` and `gbm_features.py`, `experiments/phase5.py`, `ratings/features.py` and `elo.py`, `core/asof.py`, `dataview.py` and `splits.py`, `models/rank_baseline.py`, `curation/eligibility.py`, and `evaluation/metrics.py`, `bootstrap.py` and `walkforward.py` |
| Config | `configs/experiments/phase5_gbm.yaml`, SHA-256 `cb1b5c4ef420a7bb67a826dd3f6b1fb076f06134fb9cc4749a148d24e7ca322b` (identical to the file at `b55bfe4`) |
| Original run | `d10ff67cd3b7`. Artifact SHA-256s: `test_predictions.parquet` `ef842756…9a75`, `metrics.json` `9dfe2878…b54c`, `manifest.json` `dc1f9bda…db59` |
| Package fingerprint | `76de5603b1e120a239a7763ec90820d10b12e69359039f8e230401043f204864` |
| Environment | Python 3.12.16, LightGBM 4.7.0 (checked again at run time), numpy 2.5.4, polars 2.1.0 |
| Hyperparameter grid | `num_leaves` {15, 31} × `min_data_in_leaf` {200, 500}, all at learning rate 0.05 |
| Fixed parameters | `feature_fraction` 0.8, `bagging` 0.8 / 1, `lambda_l2` 1.0; max 2,000 rounds, early stopping 50, seed 20261103 |
| Selection | Lowest symmetrised validation log-loss. Training uses years ≤ y − 3; validation uses the fit window's last year (y − 2). Ties go to the lowest grid index. The selected configuration is then refit on the full fit window with the selected number of rounds |
| Calibration | Slope-only logistic recalibration on year y − 1 |
| Symmetrisation | p = (f(x) + 1 − f(swap(x))) / 2 |
| Features | 27 features in a fixed order (19 antisymmetric, 8 shared). `level_code`, `surface_code` and `draw_code` are numeric codes |
| Population | `eval_eligible` matches (Gate-0 eligibility.v1). Walkovers and retirements are excluded |
| Missing values | LightGBM's native NaN handling. Ranks are capped at 2,000, with an indicator. Players without knowable statistics are shrunk to the knowable tour mean |

**Pinned category mapping** (from development data ≤ 2022-12-31):

| Code | Mapping |
|---|---|
| `level_code` | A 0, C 1, D 2, F 3, G 4, M 5 |
| `surface_code` | Carpet 0, Clay 1, Grass 2, Hard 3 |
| `draw_code` | challenger_main 0, challenger_qualifying 1, tour_main 2, tour_qualifying 3 |

- **Reproduction:** the mapping reproduces v1's development encoding exactly on all **390,171** rows up to 2022, with
  identical null counts. The label sets are also equal on the 2013–2022 subset, so the encoding is the same for every
  inner fold.
- **Unseen labels:** `encode_as_missing_nan`. A label absent from the mapping becomes NaN, and the late-check result
  reports the count. This policy was fixed before any late-check data was read.
- **No change to frozen behaviour was needed.** v1 sorted the loaded labels. With the late-check view (to 2024), any
  new label would shift the codes. Applying the pinned mapping replaces only those three columns, after the frozen
  `build_frame`, so no frozen file was modified.

**Provenance.** Run `d10ff67cd3b7` was executed from an uncommitted tree on top of `469d393`. The package fingerprint of
`b55bfe4`, recomputed from `git archive`, equals the run's fingerprint, so the run's code is byte-identical to
`b55bfe4`. This is resolved and recorded in the freeze record.

## 2. Confirmation rule (`configs/preregistration/phase5_gbm_v1_late_check.yaml`)

The rule's SHA-256 is `9a76d4e8ec51b167a2d273e9f946f2131885ca343e49a1ef39ed4f0aef1c5da3`. It pins the freeze record's
SHA. The runner pins both SHAs and refuses to run if either file changes.

- **Folds:** test 2023 (fit 2005–2021, calibration 2022) and test 2024 (fit 2005–2022, calibration 2023). Reading stops
  at `development_end`; the holdout is never read.
- **Primary metric:** paired log-loss difference, LightGBM v1 (recalibrated) − frozen Elo (`p_elo_overall_recal` from
  Phase 2 late-check run `2f51db502421`), pooled over 2023–2024.
- **Uncertainty:** ISO-week block bootstrap, 2,000 replicates, seed 20261103, 95 %.
- **Decision:** **CONFIRMED only if `ci_high < 0`** (strictly). Otherwise the result is NOT_CONFIRMED.
- **Secondaries:** Brier difference, calibration slope and intercept, ECE, per-year 2023 and 2024 differences, the Grand
  Slam main-draw subgroup, counts and exclusions, and unseen-label counts.
- **Sensitivity:** `without_rounds_into_draw`, the same procedure with that feature set to missing.
- None of the secondaries or the sensitivity can change the primary decision.
- **Disclosure:** written into the rule and into every result. The Elo results for 2023–24 were already examined (2 of 3
  Phase 2 looks used), so this check is blind for LightGBM only.

## 3. Runner (`src/quantedge/experiments/phase5_late_check.py`; CLI `experiment phase5-late-check`)

| Step | Behaviour (each step fails closed) |
|---|---|
| Default mode | **Dry run.** It verifies the rule, the freeze record, the frozen files, the config, the feature order, the LightGBM version, the fold schedule and the look trail. It only stats the size of the Elo baseline file and loads **no** data |
| Approval | Execution needs `--execute --approve-late-check-look --confirm-model-version phase5-gbm-v1 --note "<approval>"`. Anything else is refused before any access |
| Look limits | The registry count, `runs/late_check_audit.jsonl` and the durable backup `data/audit/late_check_audit.jsonl` must agree. One completed confirmatory look is allowed; further looks are allowed only after a recorded failure, and the registry cap of 3 always applies |
| Recording the look | `record_late_check_look` runs, then an "opened" event is appended (and fsynced) to both trails, **before** the loader is called |
| After opening | Any exception is recorded as "failed" in both trails and the look still counts. On success, "completed" is recorded with the decision and the result's SHA. Output goes to a new `runs/late_check_phase5-gbm-v1_look<N>/` directory |
| Analysis | Re-encodes the categories with the pinned mapping, then trains each fold with the frozen `train_fold` |
| Alignment | Requires exactly one row per key on both sides and identical labels. Any missing, extra or duplicate key, or any label mismatch, raises an error |

## 4. Validation (exact results)

New synthetic tests: `tests/unit/test_phase5_late_check.py`, **27 passed**. They cover:
- the real rule and freeze SHAs are pinned, and the mapping and feature order match;
- tampering with the rule, the freeze record, the config or a frozen source file refuses the run and records no look;
- feature-order and LightGBM-version checks;
- pinned encoding, unseen labels becoming NaN, and data-derived codes being replaced;
- the dry run loads nothing, records no look and creates no files (the loaders and the look recorder are patched to
  raise);
- missing approval, a wrong or empty model version, or an empty note is refused before access;
- the look appears in the registry, the audit trail and the backup before the loader is called;
- a failure after opening counts, and a second execution after completion is refused;
- three failures exhaust the cap of 3;
- deleting the audit trail, the backup or the registry refuses the run;
- frozen files are unmodified after execution;
- flipping 2023 or 2024 labels leaves that year's predictions unchanged;
- a missing, extra or duplicate key, or a label mismatch, aborts the run and the look counts;
- the decision follows `ci_high < 0` (CONFIRMED against a weak baseline, NOT_CONFIRMED against an oracle);
- the fold schedule must match the rule.

| Check | Result |
|---|---|
| Full pytest | **436 passed** |
| CI selection (`-m "not local"`) | **427 passed**, 12 deselected (after the hygiene fix) |
| Ruff check and format | Pass |
| mypy (CI scope, `src`) | Pass (67 files) |
| AT-19 rebuild (`data build`, `qa`, `audit`) | Pass offline, 0 network attempts |
| Hygiene | Pass. The first push failed CI hygiene (JSON outside `tests/`). The freeze record is SHA-pinned, so it was not reformatted; instead the hygiene guard now allows exactly `configs/frozen/phase5_gbm_v1.json`, with tests that any other JSON in `configs/frozen/` is still flagged |
| Protocol fingerprints (V1/V2) | 4 passed |
| Integrity baseline | 43/43 before and after |

## 5. Risks

1. **`rounds_into_draw` is not strictly point-in-time.** It is the minimum round over the draw's complete records, but
   it is structural in all but about 28 of about 3,900 ordinary draws and never depends on who won (development effect
   +0.0003). Mitigation: the pre-registered `without_rounds_into_draw` sensitivity. It is reported, but does not decide
   the result.
2. **Population drift against the Phase 2 Elo file.** The Elo baseline was produced on an earlier canonical build. The
   inner equivalent aligned exactly (101,587 matches), but 2023–24 alignment cannot be checked without reading late-check
   data. A mismatch aborts the run *after* the look is recorded. That look counts, and a further look is allowed only
   after the recorded failure, within the cap of 3.
3. **Elo's 2023–24 results are already known** (2 Phase 2 looks). The check is blind for LightGBM only; this is
   disclosed.
4. **In-memory loading.** `load_tables` reads every canonical table, including later rows, before `DataView` cuts them
   at `development_end`. This is the existing design (also used by Phase 2), and the runner checks the cut-off.
5. **Grand Slam under-confidence.** Pooled slope-only calibration leaves LightGBM somewhat under-confident on Slam main
   draws (development slope 1.12). This is a known calibration limitation, reported as a subgroup result.
6. **The audit trail is append-only by convention**, not by OS immutability. The backup sits on the same disk
   (gitignored `data/`). Any divergence between the trails, or a crash between the registry and audit writes, makes
   further runs refuse until the owner reconciles them. This fails closed.
7. **The runner itself is pinned by its commit**, not by a SHA inside the rule. The rule and the freeze record are
   SHA-pinned inside the runner.
8. **Grid selection uses year y − 2** (2021 and 2022), which were inner test years already inspected. This is the frozen
   v1 procedure, and nothing was chosen from late-check data.

## 6. Verdict

**READY** for one separately authorized `phase5-gbm-v1` late check.

**Not executed. Awaiting explicit owner approval.** No profitability, market or betting claim is made.
