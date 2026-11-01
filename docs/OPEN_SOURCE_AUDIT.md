# QuantEdge AI — Open-Source Audit

Date: **2026-11-01**. Scope: 4 requested projects + 10 discovered (14 total; 13 audited in depth).

## Method and rules
- Read-only inspection through the GitHub API (`gh api …/contents`, `git/trees`, `commits`) and WebFetch.
  No repository was cloned, no third-party code was executed, and no dependency was installed.
- For two repos, the auditors analysed committed CSV/notebook JSON files with **our own** `awk`/Python one-off
  scripts in the session scratchpad, which is outside this repo. No third-party code was run.
- **VERIFIED** = read in source code or data. **CLAIM** = stated only in README or docs. Code references are
  `path:line` in the audited repo at its HEAD on the audit date.
- **No code was copied into QuantEdge AI.** Only ideas are carried forward, and they are re-derived from published
  literature where one exists.
- "No licence" means all rights reserved: **no code reuse**.

---

## 1. Comparative matrix

| # | Project | Focus | Licence (verified) | Last push | Chronology / leakage | Validation & calibration | Odds & backtest realism | Tests / CI / repro | Reuse verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1 | glakshtanov-glitch/tennis-prediction-model | R: Elo + logistic betting model | **None** | 2026-06-26 | Elo sequential ✓. **Committed feature dumps have misaligned `tourney_id`** (only 1,195/14,239 train rows correct). An in-tournament feature is non-null in R128 rows, so it is corrupt or leaky | Temporal split, but edge thresholds and Elo init **chosen on the test period**. Platt calibration. No log loss | B365 only. Untimed. Duplicate join keeps **longest odds** (upward ROI bias). Flat stakes | No tests or CI. Hard-coded local paths. Not reproducible | Ideas only |
| 2 | skemp15/Tennis-Betting-Model | Notebooks: XGBoost on rolling features | **None** | 2025-04-24 | Shift-based rolling ✓. Joins inflate 91,765 → 101,218 rows. Non-temporal `cv=3` with mirrored rows | Feature/hyper-parameter selection on the test set. Market Brier (0.2042) beats model (≈0.209) | B365, no de-vig, untimed. Kelly with a bankroll floor yields £1e21 | Missing intermediate files. No seed. No tests | Ideas only |
| 3 | gmalbert/tennis-predictions | Python + Streamlit, daily retrain | **GPL-3.0** (README falsely says "permissively licensed") | 2026-11-01 (bot) | Training features online/snapshot ✓. Order by `match_num` (unreliable). **Inference/backlog uses future data** (latest Elo, all-time H2H) | Year split, **model picked by test accuracy**. No calibration step | tennis-data Avg (likely near-close) used as a **training feature**. Best-bets script calls a nonexistent method, so it outputs zero bets | No tests. Unpinned deps. Model overwritten daily | Ideas only (GPL) |
| 4 | Aneeshers/tennis-sackmann-archive | Data mirror | CC BY-NC-SA 4.0 (LICENSE summary; GitHub NOASSERTION) | 2026-06-25 | n/a | n/a | n/a | Single squashed commit. No upstream ATP SHA. No checksums | Data: non-commercial research only |
| 5 | mcekovic/tennis-crystal-ball (Ultimate Tennis Statistics) | Java/Postgres stats platform | Apache-2.0 code, but **README declares Elo customisations & prediction algorithms CC BY-NC-SA** | 2022-02-22 | Elo predict-before-update ✓. Rounds ordered by enum ✓. Synthetic end dates. Start rating may see post-event ranking. Season-wide surface factors | Tuning **and** verification on 2005→now (in-sample). Band breakdowns, no proper calibration curve | Stored prices used only for evaluation | Unit + DB integration tests, Gradle CI | Ideas from literature only. Treat their formulas as NC-SA |
| 6 | buildoak/tennis-xgboost-autoresearch | Python XGBoost "autoresearch" | MIT code, CC BY-NC-SA data | 2026-03-17 | Sorts by round ordinal ✓. **K depends on the dataset's last date** (non-causal). Country buckets from the full dataset. A/B by ID order | Single cutoff. ~60 iterations against the **same 607-match holdout**, admitted "gaming" | None | Pytest, no CI | Code reusable (MIT) but low value. Ideas |
| 7 | cran/welo (Angelini, Candila, De Angelis) | R: weighted Elo + betting rules | **GPL-3** | 2026-07-21 (mirror) | Sequential ✓. Same-day = file order. **Player filter uses whole-sample match counts** (look-ahead). Orientation by B365 favourite | Per-match BS/LL vectors. No calibration | tennis-data B365/Max/Avg. Drops retirements | CRAN checks only | Re-implement from the EJOR paper only |
| 8 | VincentAuriau/Tennis-Prediction | Python OO player-state features | MIT | 2026-06-15 | Snapshot-before-update ✓. **Order by `match_num`, which breaks on 2025+ Sackmann data** | Year-disjoint split with assert ✓. Accuracy only. An example scores on its own training set | None | Black CI only | Code reusable (MIT). Low value |
| 9 | DanielTomaro13/Tennis-Modelling | Python analytic Markov pricing (totals, handicaps) | MIT | 2026-10-04 | Elo walk-forward ✓. Elo blend weight **tuned on holdout** | LL/Brier/ECE. Its own report shows **market beats model** (0.5946 vs 0.6301 ATP) — honest | Value page uses **max price across books**, no de-vig, no timestamps. **Derived markets priced from un-anchored distributions** while match winner uses the anchored probability | No tests | Code reusable (MIT). Design lessons |
| 10 | urwishpatel2003/tennis-engine | Python exact Barnett–Clarke chain, Kalshi sizing | MIT code + data carve-out (GitHub NOASSERTION) | 2026-08-31 | **Prefix-independence leakage test** ✓. Serve rotation across sets correct ✓ | CLAIM: LL 0.6069 on 47k matches. No market comparison | Median price per side across books (may not be executable anywhere). Pre-match snapshot at start. **Forward-only bet log**. Docstring says Kelly ¼ but code defaults to full Kelly | Unit tests, no CI | Code reusable (MIT). Strong ideas |
| 11 | martiningram/tennis_monte_carlo | C++ Monte Carlo, iid vs non-iid | **None** | 2015-10-19 | n/a | n/a | n/a | Tests present. RNG seeded by time | Ideas only |
| 12 | piotr-machocki/sports-betting-research | Python de-vig + EV (Pinnacle → soft book) | **None** | 2026-10-31 | Drops non-completed matches (settlement bias) | Not done (stated) | **Records capture timestamps and inter-book gap** ✓. Historical EV is close-vs-close | Unit tests | Ideas only |
| 13 | mberk/shin | Shin de-vig library (Rust core) | MIT | 2026-08-04 | n/a | n/a | Analytic n=2 ✓. No underround guard. Silent non-convergence | Pytest + CI matrix | **Usable as dependency** (test oracle) |
| 14 | opisthokonta/implied | R de-vig catalogue (10 methods) | **GPL-3** (DESCRIPTION. No LICENSE file) | 2026-05-23 | n/a | n/a | Default Shin `js` divides by `(n−2)`, so it **fails for 2-way markets** (read, not executed) | testthat | Use as reference, re-implement from papers |

Also seen, not audited in depth: `skoval/deuce` (GPL-2). Its Elo ratings ship precomputed with no generating code.

---

## 2. Per-project findings

Verified facts and README claims are kept separate. Details and line references are in the audit notes summarised here.

### 2.1 glakshtanov-glitch/tennis-prediction-model
- **Verified:**
  - Sequential surface Elo, K=32, with rank-based start ratings (`elo_features.R:43-50,70-82`).
  - Ordering is `tourney_date, match_num`. In 2015–24 only 7 of ~640 events were out of round order.
  - The committed train/test feature dumps have `tourney_id` misaligned for most rows. Some rows point to
    tournaments dated *after* the match.
  - The in-tournament "games dominance" feature is populated for 557 of 1,593 Grand Slam R128 rows, where no prior
    in-event match can exist. When only player A has it, A wins 62.6% (train). The code that produced it is not in
    the repo.
  - Edge thresholds (`walk_forward.R:111-112`) and the Elo init variant (`elo_init_experiment.R:8`) were chosen on
    the 2020–24 evaluation period.
  - The odds join keeps the minimum implied probability among duplicates (`walk_forward.R:86`).
- **Claims:**
  - AUC 0.801 and ROI +62.7% (z=4.10): not reproducible (models not committed) and undermined by the above.
  - The repo's own later 2025 run reports +5.5% ROI, p=0.318 (`CLAUDE.md:191`).
- **Ideas:**
  - Poisson-binomial z-test of the bet win count vs implied probabilities.
  - "Edge retention" (realised ÷ claimed edge).
  - A written log of null results.
  - Accent-stripped name matching.

### 2.2 skemp15/Tennis-Betting-Model
- **Verified:**
  - Mirrored winner/loser rows with no constraint that the two probabilities sum to 1.
  - Non-temporal 3-fold CV during hyper-parameter search, with mirrored rows across folds.
  - Feature and hyper-parameter choices made on test-set AUC/Brier.
  - Joins duplicate ~10% of rows.
  - Kelly with a bankroll floor produces absurd bankrolls.
  - Three inconsistent ROI figures (README 4.78%, notebook text 2.81%, saved outputs 14.86%).
  - Player stats scraped from matchstat.com.
- **Ideas:**
  - A favourites-only baseline.
  - Model Brier vs bookmaker Brier.
  - Previous-season context splits.

### 2.3 gmalbert/tennis-predictions
- **Verified:**
  - Online features with snapshot-before-update.
  - 2nd-serve % uses total serve points as the denominator (`features.py:379`).
  - Fatigue is computed on tournament dates, so it is 0 within an event.
  - `MatchPredictor` uses each player's *latest* state, so historical "backlog" scoring leaks the future
    (`predict.py:56-102`, `compute_backlog.py:83`).
  - Selection by test accuracy (`train.py:172`).
  - Avg odds (multiplicative de-vig) used as a training feature.
  - Commits tennis-data xlsx, TML CSVs and scraped Flashscore odds.
- **Claim:** "MIT-licensed data sources" is false for tennis-data, and TML is unverified.
- **Ideas:**
  - Random row-perspective flip.
  - A daily odds snapshot that keeps opening prices.
  - An API budget guard.

### 2.4 Aneeshers/tennis-sackmann-archive
See DATA_SOURCES.md §3.1. Valuable and well-documented, but its provenance is weaker than the upstream original:
single commit, no upstream ATP SHA, no checksums, no statement of modifications.

### 2.5 mcekovic/tennis-crystal-ball
- **Verified:**
  - Predict-before-update Elo with K multipliers by level, round and best-of.
  - A rating-dependent K function and ±200 update cap.
  - Logistic inactivity decay with a COVID freeze.
  - Walkovers at half K, ABD skipped, RET at full K.
  - Separate set/game/serve/return/tie-break Elo tracks.
  - Tuning and verification both span 2005→now.
- **Claim:** "Neural network" predictor. The code shows weighted predictor items, and no NN training was found.
- **Licence nuance:** the author explicitly licenses the *algorithms* NC-SA. We use only generic, published ideas
  (e.g. K multipliers, inactivity decay) and cite literature, not their constants.

### 2.6 buildoak/tennis-xgboost-autoresearch
- **Verified:**
  - Non-causal K factor (`features.py:1172` with `elo.py:62-68`).
  - Full-dataset IOC buckets.
  - Repeated evaluation on one 607-match holdout. The README documents how the loop "gamed" the metric.
  - Correct round-ordinal sorting with an explicit note on Sackmann's 2025 `match_num` change.
- **Ideas:**
  - An immutable evaluation module with a diff check.
  - Surface Elo shrunk toward overall by match count.

### 2.7 welo (CRAN)
- **Verified:**
  - Kovalchik K `250/(N+5)^0.4`.
  - Weighted-Elo update by share of games or sets.
  - Bootstrap rating CIs.
  - Whole-sample minimum-matches filter (look-ahead selection).
  - Market-favourite orientation.
- **Ideas:** margin-weighted Elo, rating CIs, per-match loss vectors for Diebold–Mariano tests.

### 2.8 VincentAuriau/Tennis-Prediction
- **Verified:** OO player-state snapshots; ordering by `match_num` (breaks 2025+); accuracy-only metrics; a
  year-disjoint split assert.

### 2.9 DanielTomaro13/Tennis-Modelling
- **Verified:**
  - Analytic DP for the full joint games distribution.
  - Set-start server alternates by set index, which is wrong.
  - No final-set format variants.
  - Derived-market edges use un-anchored parameters while match winner uses the anchored blend.
  - EV at the max price across books.
  - Holdout-tuned blend weight.
- **Ideas:**
  - Anchoring by bisecting a symmetric serve shift.
  - Exposing full `games_dist`/`margin_dist`.
  - Returning no price outside the modelled support.

### 2.10 urwishpatel2003/tennis-engine
- **Verified:**
  - Correct parity-based set-start server.
  - Exact chain with caching.
  - Prefix-independence leakage tests.
  - A noise-aware edge gate.
  - Pre-match price snapshot at start.
  - Forward-only bet ledger ("hindsight wearing the costume of a track record").
  - Kalshi-fee sizing with a full-Kelly default contradicting the docstring.
  - The 10-point final-set tiebreak is not modelled.
- **Ideas:** most of the above.

### 2.11 martiningram/tennis_monte_carlo
- **Verified:**
  - A pluggable serve-probability interface (iid, importance-adjusted, state-indexed).
  - Every set ends in a 6-6 tiebreak, even for advantage-final-set Slams.
  - Tiebreak points attributed to the wrong server.
  - Time-seeded RNG.
- **Ideas:** the pluggable point model, and importance flags as the minimal non-iid state.

### 2.12 piotr-machocki/sports-betting-research
- **Verified:**
  - Four de-vig methods.
  - Capture timestamps per book and gap seconds.
  - The historical tennis loader keeps only completed matches.
- **Idea:** check that a signal is robust across de-vig methods.

### 2.13 mberk/shin
- **Verified:**
  - Analytic two-outcome Shin.
  - Jullien–Salanié iteration.
  - No guard for underround or non-convergence.
- **Use:** test-only oracle for our own implementation, with our own guards.

### 2.14 opisthokonta/implied
- **Verified:** a broad method catalogue with a `problematic` flag. The default Shin path divides by `(n−2)` for
  2-way markets (read, not executed).
- **Use:** literature map only.

---

## 3. Recurring failure patterns (and our countermeasure)

| Pattern | Seen in | QuantEdge countermeasure |
|---|---|---|
| Model or threshold selection on the test period | 1, 2, 3, 6, 9 | Sealed 2025+ holdout, nested walk-forward, experiment registry (VALIDATION.md §2, §8) |
| `match_num` used for chronology | 3, 8, (1) | Round ordinal + day bounds + knowability rule (ARCHITECTURE.md §5) |
| Full-dataset aggregates feeding online features | 5, 6, 7 | Forbidden-input tags, in-fold fitting (ARCHITECTURE.md §5.7) |
| Inference using latest state for historical scoring | 3 | Every prediction is computed at its own cutoff through `AsOfReader` |
| Near-close/untimed odds treated as executable or as features | 1, 2, 3, 12 | Timing classes, evidence tiers, tennis-data benchmark-only |
| Max or median-across-books price as execution | 9, 10 | Execution only at a single named book's snapshot |
| Join bias (longest odds), duplicate rows | 1, 2 | Deterministic match keys, unique-join assertions, rejected-rows ledger |
| Retirements and walkovers dropped from evaluation | 7, 12, 3 | Explicit settlement rules per market. Bets on unknown rules excluded, not silently dropped |
| Simulator format errors (set-start server, final-set rules) | 9, 11 | Parity rule, `scoring_rules.csv`, analytic = MC tests |
| Inconsistent anchoring across markets | 9 | One parameter set per match for every market |
| Unreproducible pipelines | 1, 2, 3 | uv lock, manifests, byte-identical rebuild tests |
| Committed third-party data in breach of terms | 1, 3, 9 | `data/` gitignored, CI `git ls-files` guard |
| README claims inconsistent with code | 2, 3, 5 | Reports generated from run manifests only |

---

## 4. Build-versus-adapt recommendation

**Build the core ourselves. Adapt ideas, not code. Use one small MIT library as a test oracle.**

Rationale:
1. **Licensing:**
   - Of the tennis-specific codebases, 4 have no licence and 3 are GPL.
   - One declares its algorithms NC-SA.
   - The MIT ones (6, 8, 9, 10) are small and carry design flaws we would have to rewrite anyway.
2. **Correctness:** no audited project combines a conservative chronology policy, in-fold calibration, an untouched
   holdout and timestamped execution. Retrofitting those guarantees onto foreign code costs more than building
   cleanly around them.
3. **Size:** the components (Elo, Markov DP, MC, de-vig) are each a few hundred lines. The value is in the
   contracts and tests, which no project provides.

What we take (as ideas, credited in docs, re-derived from literature):

| Idea | From | Target phase |
|---|---|---|
| Prefix/truncation-independence leakage test | 10 | P1 scaffold, P2 |
| Parity-based set-start server; exact games DP; "no price outside support" | 10, 9 | P4 |
| Anchoring by symmetric serve shift, applied to *all* markets | 9 (fixed) | P4 |
| Pluggable point-probability model (iid vs state-dependent) | 11 | P4 research |
| Kovalchik K; margin-weighted Elo; rating bootstrap CIs | 7 (paper) | P2 |
| K multipliers by level/round/best-of; inactivity decay; walkover handling | 5 (generic, literature) | P2 candidates |
| Surface Elo shrunk toward overall by match count | 6 | P2 candidate |
| Poisson-binomial bet-count test; edge-retention ratio; null-result log | 1 | P6 |
| Capture timestamps + inter-book gap; de-vig robustness across methods | 12 | P3/P6 |
| Forward-only ledger; pre-start snapshot; noise-aware edge gate | 10 | P6/P9 |
| Immutable evaluation module with diff check | 6 | P2 |
| Favourites-only and model-vs-book Brier baselines | 2 | P2/P3 |

Dependency: `shin` (MIT) as a **dev/test dependency only**, cross-checking our analytic 2-way Shin.
