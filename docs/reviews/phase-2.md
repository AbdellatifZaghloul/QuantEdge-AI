# Phase 2 review — Gate 0 audit, Elo and evaluation

Date: 2026-11-01. Code: commit `5da4a68`. Build `acc6785e9f5c2f02` (same canonical content hashes as Phase 1).
Development data only: no 2025+ holdout row was read for modelling, selection or calibration.
All figures are aggregates. Data: Jeff Sackmann / Tennis Abstract, CC BY-NC-SA 4.0, non-commercial.
**No betting or profitability claim is made.** There are no validated timestamped odds yet.

## 1. Gate 0 — anomaly audit (eligibility policy `eligibility.v1`)

### 1.1 What the Phase 1 anomalies were

**1,468 inconsistent scores (development):**

| Subclass | Count | Policy |
|---|---|---|
| Complete sets, recorded winner holds the majority, set count disagrees with `best_of` (format/label issue, e.g. best-of-5 Slam qualifying labelled best-of-3) | 1,065 | Included (`SCORE_FORMAT_MISMATCH`, flagged) |
| Final set incomplete, no terminal token (likely an unrecorded retirement) | 194 | `INCOMPLETE_UNTERMINATED` (retirement policy) |
| Non-final set incomplete (pre-1975 formats or typos) | 110 | `INCOMPLETE_UNTERMINATED` (retirement policy) |
| Recorded winner won fewer sets | 52 | Excluded (`SCORE_CONTRADICTION`) |
| Sets tied | 46 | Excluded (`SCORE_CONTRADICTION`) |
| Walkover with a stray set token | 1 | `WALKOVER` |

**174 players appearing twice in one knockout round** (148 in development):

| Resolution | All years | Development |
|---|---|---|
| Shared placeholder identity `sackmann:199999` | 68 | 68 |
| Round-label conflict (different opponents): kept, knowability deferred to tournament end, no earlier-round precedence, not evaluated | 43 | 43 |
| Contradictory duplicates (same pair, reversed winners): excluded | 14 | 14 |
| Duplicate records (same pair, same winner): one kept deterministically | 12 | 10 |
| Became consistent once unscored ghost rows and duplicates were removed (`OK`) | 23 | 10 |
| Unscored rows inside a conflict (`SCORE_MISSING`) | 12 | 1 |
| Retirement inside a conflict (retirement policy and deferral) | 2 | 2 |

**93 appearances after a knockout loss (development):**

| Category | Count |
|---|---|
| Placeholder identity shared by different real players | 74 |
| Team events (ATP Cup, United Cup, Nations Cup), where an individual loss does not eliminate: legitimate | 8 |
| Resolved by the duplicate policy | 4 |
| Round-label conflict (deferred) | 2 |
| Unresolved knockout contradictions: all involved matches excluded | 5 |

**Identity finding:**
- `sackmann:199999` ("U Unknown", 107 matches, 1968–2007) and `sackmann:200816` ("Bye1", 2 matches) are placeholders,
  not players. Both are curated in `reference/placeholder_players.csv`.
- Matches with a placeholder never update ratings and are never evaluated.
- A rule-based scan found no other placeholder candidate used in matches.

### 1.2 Policies (deterministic, row-order independent, tested)

| Reason | Rating update (baseline) | Retirement-sensitivity update | Evaluated |
|---|---|---|---|
| OK, `SCORE_FORMAT_MISMATCH` | yes | yes | yes |
| `ROUND_LABEL_CONFLICT` | yes, knowable only after the tournament's end bound | yes | no |
| `RETIRED`, `DEFAULT_PLAYED`, `INCOMPLETE_UNTERMINATED` | no | yes | no |
| `WALKOVER`, `DEFAULT_UNPLAYED`, `ABANDONED`, `SCORE_MISSING` / `UNCERTAIN` / `MALFORMED` | no | no | no |
| `PLACEHOLDER_PLAYER`, `DUPLICATE_RECORD`, `CONTRADICTORY_DUPLICATE`, `KNOCKOUT_CONTRADICTION`, `SCORE_CONTRADICTION` | no | no | no (provenance and reason retained) |

**Effect (all matches with start date ≤ 2024-12-31, including burn-in):** 418,335 matches.

| Primary reason | Count |
|---|---|
| OK | 403,616 |
| RETIRED | 10,551 |
| WALKOVER | 2,104 |
| SCORE_FORMAT_MISMATCH | 1,065 |
| INCOMPLETE_UNTERMINATED | 304 |
| SCORE_MISSING | 124 |
| DEFAULT_PLAYED | 119 |
| DEFAULT_UNPLAYED | 113 |
| PLACEHOLDER_PLAYER | 109 |
| SCORE_CONTRADICTION | 98 |
| ROUND_LABEL_CONFLICT | 43 |
| ABANDONED | 38 |
| CONTRADICTORY_DUPLICATE | 14 |
| SCORE_UNCERTAIN | 14 |
| DUPLICATE_RECORD | 10 |
| KNOCKOUT_CONTRADICTION | 10 |
| SCORE_MALFORMED | 3 |

Decisions:
- Baseline rating updates: 404,724.
- Retirement-variant updates: 415,698.
- Evaluation-eligible: 404,681, of which 202,090 fall in 2005–2024.
- Deferred knowability: 44.

Holdout (integrity counts only): 21,518 matches.

| Primary reason | Count |
|---|---|
| OK | 20,591 |
| RETIRED | 781 |
| WALKOVER | 124 |
| SCORE_MISSING | 15 |
| SCORE_CONTRADICTION | 3 |
| DUPLICATE_RECORD | 2 |
| ABANDONED | 1 |
| DEFAULT_PLAYED | 1 |

**Gate result:** no unresolved chronology or identity issue blocks modelling.

**Residual risk:**
- A single mislabelled round that creates no visible conflict would get day bounds that are too early.
- The day-bound templates are still unvalidated heuristics (D-18).

## 2. Models

**Elo.** Expected score:

```
E_a = 1 / (1 + 10^((R_b - R_a) / 400))
R_a <- R_a + K_a (S_a - E_a)
```

- Initial rating is 1500.
- K is either constant or the FiveThirtyEight form `K = c / (n + 5)^0.4`, where `n` is the player's number of prior
  rated matches.
- K is per player, so updates are not forced to be zero-sum.

**Surface Elo.**
- Separate tracks per surface (Hard, Clay, Grass, Carpet), using the same formula on surface matches only.
- Prediction uses partial pooling toward the overall rating:

  ```
  R_eff = w R_surface + (1 - w) R_overall,   w = n_s / (n_s + m)
  ```

  where `n_s` is the player's number of prior surface matches and `m` is a pseudo-count (empirical-Bayes-style
  shrinkage). `m = 0` gives pure surface Elo; `m = ∞` gives overall Elo.

**Decay (sensitivity only).**

```
R(t) = 1500 + (R - 1500) * 0.5^(max(0, days_inactive - g) / h)
```

**Chronology.**
- Ratings come from `quantedge.core.asof.replay`.
- A result updates state only when it is eligible and knowable (K), or through the earlier-round exception (P,
  outcome fields only).
- Results that become knowable in the same batch are applied simultaneously, from the pre-batch state.
- Overlays are copy-on-write, and each prediction is read before its own result.

**Ranking baseline.**

```
P(A) = sigmoid(b1 (log r_A - log r_B) + b2 (u_A - u_B))
```

- `r` is the latest ranking knowable before the cutoff, capped at 2000.
- `u` flags players who are unranked or whose ranking is more than 365 days old.
- There is no intercept, because A/B orientation is random.

## 3. Protocol

Inner folds use test years 2013–2022.

- **Fit/selection window:** 2005 to (y−2).
- **Calibration year:** y−1.
- **Test year:** y.

In each fold:
- The Elo candidate (7 K settings, and for surface Elo × `m` ∈ {0, 5, 20, 50, ∞}) is selected by log loss on the fit
  window.
- A slope-only recalibration `p' = sigmoid(b · logit p)` is fitted on y−1.
- The ranking baseline is fitted on the same windows.
- All models are scored on the identical eligible population: 101,587 inner test matches.

Uncertainty: block bootstrap over tournament weeks (450 blocks, 2,000 replicates, seed 20261101).

## 4. Results — inner folds 2013–2022 (run `0a5b056019ec`, clean tree)

| Model | Log loss [95% CI] | Brier | Accuracy | ECE | Calibration slope |
|---|---|---|---|---|---|
| Coin (0.5) | 0.6931 | 0.2500 | 0.500 | — | — |
| Ranking, recalibrated | 0.6233 [0.6203, 0.6263] | 0.2173 | 0.647 | 0.009 | 0.99 |
| Elo overall, raw | 0.6165 [0.6132, 0.6202] | 0.2139 | 0.658 | 0.028 | 0.81 |
| **Elo overall, recalibrated** | **0.6132 [0.6104, 0.6164]** | 0.2129 | 0.658 | 0.003 | 0.99 |
| Surface Elo, recalibrated | 0.6136 [0.6105, 0.6166] | 0.2130 | 0.658 | 0.003 | 0.99 |

Paired differences (block bootstrap):

| Comparison | Δ log loss [95% CI] | Δ Brier [95% CI] |
|---|---|---|
| Elo (recal) − Ranking (recal) | −0.0101 [−0.0121, −0.0080] | −0.0045 [−0.0053, −0.0036] |
| Surface Elo (recal) − Ranking (recal) | −0.0098 [−0.0118, −0.0076] | −0.0043 [−0.0052, −0.0033] |
| Surface Elo (recal) − Elo (recal) | +0.0003 [−0.0005, +0.0012] | +0.0002 [−0.0002, +0.0005] |
| Elo (raw) − Ranking (raw) | −0.0071 [−0.0094, −0.0047] | −0.0035 [−0.0044, −0.0025] |

**Selection:** FiveThirtyEight `c = 250` was chosen for overall Elo in every fold, and `m = 50` for surface Elo in
every fold.

**Calibration:**
- Raw Elo is over-confident. The recalibration slope fitted on y−1 falls from 0.88 (2013) to 0.69 (2021).
- Recalibration removes most of the ECE.

**Per fold:**
- Elo beats the ranking baseline in 9 of 10 test years.
- In 2016 it is worse: 0.6091 vs 0.6035.
- 2020 is a reduced season (4,595 test matches).

Figures:
- [reliability, recalibrated](figures/phase2/inner_reliability_recalibrated.png)
- [reliability, raw](figures/phase2/inner_reliability_raw.png)
- [log loss by fold](figures/phase2/inner_log_loss_by_fold.png)

**Late check 2023–2024** (run `2f51db502421`, look 2 of 3, same frozen protocol), n = 27,134:

| Model | Log loss |
|---|---|
| Ranking (recal) | 0.6399 |
| Elo (recal) | 0.6267 |
| Surface Elo (recal) | 0.6273 |

- Δ log loss, Elo − Ranking: −0.0132 [−0.0166, −0.0097]. Δ Brier: −0.0056 [−0.0071, −0.0041].
- Surface − overall: +0.0007 [−0.0008, +0.0022].
- [Late-check reliability](figures/phase2/late_check_reliability_recalibrated.png).
- **Look 1** was an informal development printout that included 2023–24 pooled log loss. No decision was based on
  it, and it is recorded in `runs/late_check_looks.json`.

**Interpretation.**
- Elo is a clear, stable improvement over a point-in-time ranking model.
- Partially pooled surface Elo adds nothing measurable on this population.
- Raw Elo needs recalibration.
- These are probability-quality results on all eligible tour, qualifying and Challenger matches. They say nothing
  about bookmaker markets.

## 5. Sensitivity (descriptive, inner folds; never used for selection)

Pooled log loss, raw / per-fold recalibrated (identical 101,587-match population):

| Variant | Raw | Recalibrated |
|---|---|---|
| Reference: 538 c=250, conservative ordering, retirements excluded | 0.6165 | 0.6132 |
| K: 538 c=150 / 350 | 0.6186 / 0.6207 | 0.6182 / 0.6126 |
| K constant: 16 / 24 / 32 / 48 | 0.6264 / 0.6218 / 0.6206 / 0.6231 | 0.6263 / 0.6216 / 0.6194 / 0.6183 |
| Surface m = 0 / 5 / 20 / 50 / ∞ | 0.6265 / 0.6212 / 0.6171 / 0.6150 / 0.6165 | 0.6214 / 0.6182 / 0.6153 / 0.6136 / 0.6132 |
| Decay h = 365 d, no grace | 0.6563 | 0.6554 |
| Decay h = 730 d, no grace | 0.6425 | 0.6416 |
| Decay h = 365 d, 60-day grace | 0.6189 | 0.6189 |
| Decay h = 730 d, 60-day grace | 0.6161 | 0.6157 |
| Retirements included in updates | 0.6146 | 0.6121 |
| Naive ordering (leakage exposure) | 0.6141 | 0.6113 |

Readings:
- Pure surface Elo (`m = 0`) is clearly worse. Pooling is necessary.
- Continuous decay toward 1500 is harmful. Inactivity-only decay is neutral to slightly worse.
- Using retirements in updates improves log loss by about 0.001. This is a candidate change for a future model
  version, to be decided before any holdout use.
- Naive ordering scores about 0.002 better. That is the size of the information the conservative policy withholds:
  partly leakage, partly genuinely knowable results whose timing the data cannot prove. The conservative variant
  stays official.

## 6. Experiments run (accounting)

| Run | Description |
|---|---|
| `9c06007e11c4` | Exploratory, pre-test code. Artifacts deleted; recorded in the registry with its headline numbers |
| `0a5b056019ec` | Official inner folds |
| `2f51db502421` | Late check (look 2) |
| Look 1 | Informal timing printout |

Synthetic test runs live in temporary directories.

## 7. Tests

- **Local (Windows):** 209 passed, including 10 real-data tests.
- **CI run [41351231361](https://github.com/AbdellatifZaghloul/QuantEdge-AI/actions/runs/41351231361), commit
  `5da4a68`:** Ubuntu and Windows each 199 passed, 2 skipped. The skips are the real-data modules, which skip
  themselves without `data/`.

New Phase 2 tests:

| Area | What is tested |
|---|---|
| Elo maths | Expected score, K, zero-sum constant K, simultaneous batches, overlay isolation, decay, pooling |
| Leakage | Own outcome never changes own ratings; future perturbation; same-day isolation (and the naive variant demonstrably leaking); excluded results never update; deferred knowability; row-order invariance; antisymmetry |
| Gate 0 | Every policy, deterministic duplicate choice, team events, clean evaluation population, row-order independence |
| Evaluation | Metrics, recalibration recovering a known slope, seeded block bootstrap, point-in-time ranks (same-day and stale excluded), selection never using the test year, run IDs, late-check cap |
| End-to-end | Synthetic 10-season build with a sealed holdout that never reaches predictions; registry and figures |

## 8. Limitations

- **Population:** evaluation covers all eligible tour, qualifying and Challenger matches. Results for tour-level-only
  or odds-covered subsets will differ. Comparisons with bookmakers require Phase 3.
- **Sensitivity is descriptive:** the studies use the inner test folds, so their best variants must not be adopted
  without a new pre-registered model version.
- **Retirements:** their exclusion from evaluation follows the policy, not market settlement rules.
- **Day bounds:** the templates are still unvalidated (D-18).
- **Surface:** surface is missing for 377 tournaments, and those matches use overall ratings only.
- **Holdout:** not opened. The ranking baseline uses only rank, not points.

## 9. Reproduce

```bash
uv sync --locked
uv run quantedge data fetch && uv run quantedge data build && uv run quantedge data qa
uv run quantedge data audit
uv run quantedge experiment run          # inner folds; writes runs/<run_id>/
uv run quantedge experiment list
uv run pytest
```

The late check (`uv run quantedge experiment late-check --note "..."`) has one look remaining for `phase2-elo-v1`.
