# QuantEdge AI — Roadmap

Status: **Phase 3A accepted; Phase 3B (pilot preparation, timing hardening, pre-registration) complete 2026-11-01 — awaiting owner approvals A1–A8 in [reviews/phase-3b.md](reviews/phase-3b.md) §6 before any chargeable request.**
Each phase ends with `docs/reviews/phase-N.md` (real counts, deviations, open issues) and owner approval.
Decision IDs refer to [DECISIONS.md](DECISIONS.md).

## Dependency graph

```
P0 / P0.5 Design + audit
 └─ P1 Data foundation & chronology ──┬─ P2 Elo + evaluation harness ──┬─ P4 Serve/return, Markov/MC, pricing
     (D-05,10,11,12,13,14,17)         │   (D-09, D-18)                  │    (D-19)
                                      │                                  └─ P5 Calibrated ML + uncertainty
                                      └─ P3 Odds: probe, ingest, market baseline ─┬─ P6 Backtest & execution (D-20)
                                          (D-03b, D-06); start forward capture    └─ P7 Portfolio (D-21)
                                                                                        └─ P8 API, dashboard, Docker
                                                                                              └─ P9 Prospective paper trading (D-04)
```

P3 can run in parallel with P2 once P1 is done. Forward odds capture starts as early as D-06 allows, because
timestamped data cannot be collected retroactively.

## Phases (summary)

| Phase | Deliverables | Acceptance (summary) |
|---|---|---|
| **P1** Data foundation | Tooling, CI, pinned Sackmann-archive ingestion, canonical tables, identity, orientation, chronology fields, information-set + AsOfReader scaffold, holdout guard, licence lineage, QA report | Full test list below |
| **P2** Elo + harness | Elo/surface Elo under the batch + overlay semantics; walk-forward splitter; metrics; block bootstrap; run manifests; experiment registry; immutable evaluation module | Leakage suite §5.2 passes; Elo beats in-fold rank baseline with CI on development (or this is reported honestly); naive-vs-conservative sensitivity reported |
| **P3** Odds | Odds API probe (D-06); timestamped adapter (`COMMERCIAL_LICENSED`); tennis-data private reader (`RESEARCH_PRIVATE`, manual files); crosswalk; odds QA; de-vig (own implementation, `shin` oracle); market baseline; day-bounds validation against `commence_time` | Link rate and every failure categorised; timing class on every row; `RESEARCH_PRIVATE` artifacts unexportable (test); day-bound violations = 0 after widening |
| **P4** Simulation & pricing | Shrunk serve/return; `scoring_rules.csv` with citations; exact DP (parity serve order, final-set rules); MC with posterior draws; single parameter set across all markets; "no price outside support" | Analytic = MC within error; identity tests; PIT/coverage/CRPS vs empirical baseline on development |
| **P5** Calibrated ML | Logistic + LightGBM; independent and market-anchored tracks; in-fold calibration; intervals; market-shrinkage weight | Pre-declared margin over baseline with CI; experiment count reported; **holdout opened once** for the frozen version |
| **P6** Backtest | Decision-time engine; execution model with sensitivity grid; settlement rules with citations; CLV; tiers | No snapshot after decision+latency or commence can be used (test); Max/Avg/median execution impossible (test) |
| **P7** Portfolio | Scenario log-growth optimiser; fractional Kelly benchmarks; caps; drawdown governor; no-bet rules | Zero edge → zero stake; caps respected (property tests); correlated same-match bets get less combined stake than independent Kelly |
| **P8** Serving | Read-only FastAPI; Streamlit; Docker; README attribution | Contract tests; lineage filter blocks `RESEARCH_PRIVATE`; no data in image |
| **P9** Prospective | Forward capture of odds + results; append-only paper ledger; weekly CLV | Every entry reproducible from snapshot + manifest; no backfill (test) |

---

## Phase 1 — Implementation specification (revised)

**Goal:** a deterministic, tested pipeline from a pinned research snapshot to validated canonical tables carrying
the identity, orientation, chronology and licence metadata that every later phase relies on.
**Out of scope:** ratings, features beyond test scaffolds, models, odds, API, dashboard, Docker.

### 1.1 Prerequisites
Decisions D-05, D-10, D-11, D-12, D-13, D-14 and D-17 accepted. uv installed.

### 1.2 Files to create
```
pyproject.toml  uv.lock  .python-version(3.12)  .env.example  .gitignore(update)  .pre-commit-config.yaml
.github/workflows/ci.yml               # ubuntu-latest + windows-latest
configs/sources.yaml  configs/splits.yaml  configs/chronology.yaml
src/quantedge/reference/{round_ordinal,round_day_bounds,tournament_classes,score_tokens,level_codes}.csv
                                       # package data; each with a rationale/description column
src/quantedge/core/{ids.py, hashing.py, chronology.py, asof.py, dataview.py, manifest.py, lineage.py}
src/quantedge/adapters/base.py
src/quantedge/adapters/research/sackmann_archive.py
src/quantedge/canonical/{schemas.py, score.py, rounds.py, players.py, build.py}
src/quantedge/quality/report.py
src/quantedge/cli.py                   # quantedge data fetch | build | qa
tests/fixtures/*.csv                   # hand-written synthetic rows only
tests/golden/orientation_vectors.csv
tests/{unit,property,integration,leakage}/...
docs/reviews/phase-1.md               # written after the real build
```

### 1.3 Behaviour
- **Fetch:**
  - Downloads only the D-11 files from raw URLs pinned to commit `83733587353df8a41f2fd4f516147d5aa83f5a8d` into
    `data/raw/research_nc/sackmann_archive/<sha>/`.
  - Writes `manifest.json` (URL, commit, bytes, SHA-256, retrieved_at, licence = CC BY-NC-SA 4.0, attribution text).
  - Idempotent. Verifies existing files by hash.
- **Build:**
  - Reads with explicit dtypes and pivots winner/loser columns into per-player rows.
  - Normalises score tokens and assigns IDs, `match_key` and orientation (ARCHITECTURE.md §4).
  - Computes chronology fields (§5.2–5.4) and writes canonical Parquet + `rejected_rows` to
    `data/processed/<build_id>/`, with a build manifest (input manifest hash, git SHA, content hashes,
    `licence_lineage`).
- **QA:**
  - Integrity checks on all years.
  - Outcome-dependent statistics on development years only.
  - Writes `qa_report.json` and `qa_report.md`.

### 1.4 Acceptance tests

CI runs on synthetic fixtures. Tests marked **[local]** run on the real snapshot and their outputs are recorded in
`docs/reviews/phase-1.md`.

| ID | Test | Pass condition |
|---|---|---|
| AT-01 | Environment | `uv sync --locked` succeeds on ubuntu-latest and windows-latest with Python 3.12 |
| AT-02 | Static quality | `ruff check`, `ruff format --check`, `mypy --strict src/quantedge/{core,canonical,adapters}` all clean |
| AT-03 | Fetch manifest | After fetch, every file in the manifest exists and its SHA-256 matches. A second fetch performs 0 downloads. A tampered file makes fetch exit non-zero naming the file. **[local]** |
| AT-04 | No network in tests | The test suite runs with sockets blocked. Any network attempt fails the test |
| AT-05 | Schemas | Every canonical table validates against its Pandera schema (fixtures and **[local]**) |
| AT-06 | Reconciliation | For every source file: `rows_in = rows_canonical + rows_rejected`, exactly. Every rejected row has a `reason_code` from the closed enum |
| AT-07 | Key uniqueness | Zero duplicate `match_key` in `matches`. Collisions go to `rejected_rows` with `reason_code=KEY_COLLISION` |
| AT-08 | Winner-swap invariance | Property test (hypothesis, ≥ 1,000 generated rows): swapping winner/loser fields leaves `match_key`, `player_a_id`, `player_b_id` unchanged and flips `a_won` |
| AT-09 | Golden orientation vectors | `tests/golden/orientation_vectors.csv` (≥ 50 rows) reproduces exactly on both CI OSes |
| AT-10 | Row-order invariance | Building from row-shuffled fixtures gives identical canonical content hashes |
| AT-11 | No outcome columns downstream | Schema scan: no canonical column other than `matches.winner_id`/`a_won` encodes the outcome. No column name contains `winner_`/`loser_`/`w_`/`l_` prefixes |
| AT-12 | Orientation statistics | **[local]** on completed development matches: global exact binomial p ≥ 0.001; stratified G-test (Holm, family α = 0.001) passes; higher-ranked-is-A binomial p ≥ 0.001. **CI:** the mutation test (55% winner-as-A on 10,000 synthetic matches) **fails** the global test as expected |
| AT-13 | Score parser | ≥ 40 hand-written cases (normal, `7-6(5)`, match tiebreak `[11-9]`, `RET`, `W/O`, `Walkover`, `DEF`, best-of-5, malformed) parse exactly as expected. Generated legal scores round-trip. Unknown tokens yield `outcome_type=unknown` + QA entry, never an exception. **[local]** every distinct unknown token is listed |
| AT-14 | Round ordinal | Every observed `round` maps to an ordinal or is rejected `UNKNOWN_ROUND`. Within a tournament, each player's rounds are strictly increasing (except RR). Violations are quarantined |
| AT-15 | Chronology fields | For every match: `match_day_lo ≤ match_day_hi ≤ tournament_end_bound`; `prediction_cutoff_utc < knowable_at_utc`; day-instant conversions match the UTC+14/UTC−12 rules (unit tests with explicit expected instants) |
| AT-16 | Information-set rule | Synthetic scenarios, each asserting exact membership of I(m): (a) same-day matches excluded from each other; (b) own previous round included via P; (c) another player's same-round result excluded; (d) overlapping-event result excluded until knowable; (e) RR matches have no mutual precedence; (f) ranking dated on the match's Monday excluded |
| AT-17 | AsOfReader leakage scaffold | A trivial stateful feature (prior wins count) on fixtures satisfies: future-perturbation invariance; truncation equivalence; batch-order invariance |
| AT-18 | Holdout guard | `DataView(mode="development")` raises on any read touching `tournament_start_date ≥ 2025-01-01`. The QA report's holdout section contains only integrity fields (asserted by key whitelist) |
| AT-19 | Determinism | Two builds from the same raw manifest give identical canonical content hashes per table (D-12). **[local]** and CI |
| AT-20 | Licence lineage | Every build manifest lists `licence_lineage = {RESEARCH_NC}`. Loading the build under the `commercial` profile raises `LicenceError` |
| AT-21 | Repo hygiene | CI step: `git ls-files` contains nothing under `data/` or `runs/`, no `.env`, no `*.parquet`, and no `*.csv` outside `tests/` and `reference/`. A pre-commit secret scan is configured |
| AT-22 | Snapshot end confirmed | **[local]** The maximum `tourney_date` in the ingested data is recorded in the phase review (expected ≈ 20260525, not assumed) |
| AT-23 | QA report | **[local]** Coverage tables (year × level × field), rejection counts by reason, unknown codes, stats-consistency violations (`first_won ≤ first_in ≤ serve_pts`, `bp_saved ≤ bp_faced`, …) and players in overlapping events, all with real counts |

Additional tests required at P1 approval (2026-11-01):

| ID | Test | Pass condition |
|---|---|---|
| AT-24 | Key separation | `match_key` differs whenever any one of tournament ID, season, draw category, round or the player pair differs (unit + property tests). The same pair meeting in different seasons or draw categories never collides |
| AT-25 | Collision resolution | Fixtures with (a) exact duplicates → one kept, others `DUPLICATE_ROW`; (b) collisions with distinct `match_num` → distinct keys, independent of row order; (c) collisions without distinct `match_num` → all `KEY_COLLISION` |
| AT-26 | `match_num` independence | Permuting `match_num` among non-colliding rows within each tournament changes nothing except `source_match_num`: identical keys, orientation, chronology fields and information sets |
| AT-27 | Point-in-time contract | No admissible result for any match has `knowable_at_utc > prediction_cutoff_utc` unless it is a (P) earlier-round result of a participant. (P) results expose outcome fields only, never `match_stats` |
| AT-28 | Splits | All configured folds end ≤ 2024-12-31, are chronological, and fit/calibration/test windows are disjoint and ordered |
| AT-29 | Canonical player IDs | Source IDs with whitespace or leading zeros map to the same canonical ID. Non-integer IDs are rejected with a reason |

### 1.5 Definition of done
All AT-01…AT-29 pass. `docs/reviews/phase-1.md` records the local results and any deviations. No modelling code
exists. Changes are reviewed by the owner **before** any commit or push.
