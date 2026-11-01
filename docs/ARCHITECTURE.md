# QuantEdge AI — Architecture

Status: **Phase 0.5 revision** (2026-11-01). Scope: **ATP men's singles only**.
Companion docs: [DATA_SOURCES.md](DATA_SOURCES.md), [VALIDATION.md](VALIDATION.md), [ROADMAP.md](ROADMAP.md),
[DECISIONS.md](DECISIONS.md), [OPEN_SOURCE_AUDIT.md](OPEN_SOURCE_AUDIT.md).

QuantEdge AI is a research platform. It produces probabilities, fair prices and *paper* staking analyses.
It contains **no bet-placement code** and never will (D-01).

---

## 1. Design principles

1. **Knowability over ordering.** A result may inform a prediction only if it was *provably knowable* at the
   prediction cutoff (§5). A sort order alone proves nothing.
2. **Licence lineage.** Every artifact knows which licence classes it derives from (§7).
3. **Component-appropriate baselines.** Each component is judged against the baseline for its own task.
   Bookmaker prices are the benchmark wherever contemporaneous odds exist (VALIDATION.md §3).
4. **Observed ≠ executable.** Odds snapshots are observations. Execution is a modelled, unverified assumption.
5. **Files over services.** Parquet + DuckDB on local disk. No servers, queues, or orchestrators.
6. **Determinism.** Identical inputs give byte-identical outputs on any machine. No dependence on Python
   `hash()`, dict order, row order, or wall clock.

---

## 2. Technology stack

| Concern | Choice | Notes |
|---|---|---|
| Python | **3.12**, pinned in `.python-version` | The machine has 3.14. 3.12 is lowest-risk for scientific wheels (D-10) |
| Env + lock | **uv** (`pyproject.toml`, `uv.lock`) | Not yet installed locally |
| DataFrames | **Polars** | Explicit dtypes everywhere |
| Storage / SQL | **Parquet** + **DuckDB** (embedded) | |
| Validation | **Pandera** (polars) for tables, **Pydantic v2** for configs and API | |
| CLI | **Typer** | |
| Numerics | NumPy, SciPy (numba only if profiling demands) | |
| ML | scikit-learn, LightGBM | No deep learning in v1 |
| De-vig | Own implementation. `shin` (MIT, mberk) as a **test-only oracle** | OPEN_SOURCE_AUDIT.md §4 |
| Optimisation | cvxpy | |
| Tracking | Run manifests (JSON) in `runs/` | No MLflow |
| API / UI | FastAPI (read-only), Streamlit | From P8 |
| Quality | ruff, mypy, pytest, hypothesis, pre-commit | |
| CI | GitHub Actions, matrix `ubuntu-latest` + `windows-latest` | Cross-OS determinism (§4.4) |
| Containers | One Dockerfile + compose | From P8. Docker not installed locally |

Not used: Airflow/Prefect, Kafka, Postgres, Kubernetes, feature-store products, cloud infrastructure.

---

## 3. Repository structure

```
QuantEdge-AI/
├── pyproject.toml  uv.lock  .python-version  .env.example
├── configs/                      # sources, splits, chronology, elo, backtest, portfolio (YAML)
├── reference/                    # COMMITTED, hand-curated, cites sources; no licensed rows
│   ├── round_ordinal.csv
│   ├── round_day_bounds.csv      # day-offset bounds per (event class, round), §5.4
│   ├── tournament_classes.csv    # level/draw → duration class
│   ├── score_tokens.csv          # RET, W/O, Walkover, DEF, ABD …
│   └── scoring_rules.csv         # final-set format by tournament-year (P4)
├── data/                         # GITIGNORED, namespaced by licence class
│   ├── raw/{research_nc,research_private,commercial,own}/<source>/<version>/
│   └── processed/<build_id>/
├── runs/                         # GITIGNORED
├── src/quantedge/
│   ├── core/                     # ids, hashing, time & chronology, as-of reader, manifests, licence lineage
│   ├── adapters/
│   │   ├── research/             # sackmann_archive.py, tennis_data.py (private), tml.py (optional)
│   │   ├── commercial/           # odds_api.py; future licensed results feeds
│   │   └── base.py               # Adapter protocol: emits canonical tables + licence class
│   ├── canonical/                # schemas (pandera), builders, score parser, identity crosswalks
│   ├── quality/                  # QA checks and reports
│   ├── features/  ratings/  markets/  models/  simulation/  pricing/
│   ├── evaluation/  backtest/  portfolio/
│   ├── api/  cli.py
├── dashboard/  notebooks/  tests/{unit,property,integration,leakage,golden}/  docs/
```

Adapters are the **only** modules that know source formats. Everything downstream consumes canonical tables.
A future commercial results feed is a new adapter under `adapters/commercial/` with no downstream changes.

Dependency direction: `core ← adapters/canonical ← features/ratings ← models/simulation ← pricing ← backtest ← portfolio ← api/dashboard`.

---

## 4. Canonical data model

All timestamps are UTC. All dates are ISO.

### 4.1 Stable identifiers

| Entity | ID rule | Why |
|---|---|---|
| Player | `"{source_ns}:{int(source_player_id)}"`, e.g. `sackmann:104925`. The integer form strips leading zeros and whitespace. A crosswalk table links namespaces | Canonical, stable across builds. Never an auto-increment |
| Tournament edition | `"{source_ns}:{tourney_id}"`, e.g. `sackmann:2019-560` | The source key already encodes the season |
| Match | `match_key = "m1_" + hex(sha256(LP("qe.match.v1") ‖ LP(tournament_id) ‖ LP(season) ‖ LP(draw_category) ‖ LP(round) ‖ LP(min(pa,pb)) ‖ LP(max(pa,pb))))[:32]`. `LP(x)` is the length-prefixed UTF-8 encoding of `x`, `season` is the source's season (the year of the source file), and `draw_category` is `{tour,challenger,itf,other}_{main,qualifying}` | Built from the **unordered** canonical player pair and the event context, so no outcome field can enter it. Length prefixing makes field boundaries unambiguous |

**Deterministic collision resolution** (applied per group of rows sharing a base key):
1. Rows with identical full source content are exact duplicates. The first in `(file priority, file name)` order is kept and the others are rejected `DUPLICATE_ROW`. The kept row's content is identical either way.
2. If a single row remains, its key is the base key.
3. If several remain and **all** have distinct non-null `match_num`, each key becomes `sha256(base_key ‖ "match_num" ‖ match_num)`.
   This is the only use of `match_num` in identity. It is a discriminator, not chronology.
4. Otherwise every row in the group is rejected `KEY_COLLISION`. We never guess.

The rule depends only on row content, never on row position or outcome. Shuffling input rows gives identical keys.

**`match_num` policy:** it is stored as `source_match_num`, an *ordering hint only*. It is never used to decide
chronology or knowability, and permuting it (outside collision groups) changes no derived field.

### 4.2 Deterministic, winner-independent orientation

```
bit       = first byte of sha256("qe.orient.v1" ‖ match_key) & 1
player_a  = min(p1, p2) if bit == 0 else max(p1, p2)
```
- The inputs are the unordered pair and the event context only. **No outcome field enters the function**, so
  orientation is independent of the winner by construction, not just in expectation.
- SHA-256 over UTF-8 bytes with explicit separators is identical across OS, Python version and process.
  `hash()` is forbidden.
- Versioned salt (`v1`). Changing it is a breaking data change requiring a new build ID.
- Winner-first source columns (`winner_*`, `loser_*`, `w_*`, `l_*`) are pivoted to per-player rows
  **before** orientation. No downstream table contains the words winner or loser except `matches.winner_id`.

### 4.3 Tables

- **`players`**: `player_id`, crosswalk IDs, names, `hand`, `birth_date`, `height_cm`, `country`. Static fields only.
- **`tournaments`**: `tournament_id`, `name`, `level`, `surface`, `indoor` (nullable), `start_date`,
  `draw_size`, `best_of`, `event_class` (→ `round_day_bounds`), `end_date_bound`.
- **`matches`**: `match_key`, `tournament_id`, `round`, `round_ordinal`, `player_a_id`, `player_b_id`,
  `winner_id`, `a_won` (bool), `outcome_type` (completed | retired | walkover | default | abandoned | unknown),
  `score_raw`, parsed set/game scores, `best_of`, `minutes`, all chronology fields (§5.2),
  `licence_class`, `source`, `source_row_sha256`.
- **`match_entry`** (pre-event facts per match × player): `seed`, `entry`, `rank_at_event_start`, `age_at_event_start`.
  These are pre-event, not pre-match. Labelled as such.
- **`match_stats`** (post-match, per match × player): serve and return counts. Never usable for the same match.
- **`rankings`**: `player_id`, `ranking_date`, `rank`, `points`, `knowable_at_utc` (§5.3).
- **`odds_snapshots`**: `source`, `bookmaker`, `market`, `selection`, `line`, `decimal_odds`,
  `observed_at_utc` (nullable), `timing_class` (`timestamped` | `untimed_near_close`), `event_commence_utc`
  (nullable), `ingested_at_utc`, `licence_class`.
- **`rejected_rows`**: `source_file`, `row_number`, `source_row_sha256`, `reason_code`, `detail`.

Later phases add `predictions`, `fair_prices`, `paper_bets`, each carrying `prediction_cutoff_utc`,
`run_id` and `licence_lineage`.

### 4.4 Orientation guarantees (tested)
1. **Winner-swap invariance:** swapping winner/loser columns in a raw row leaves `match_key`, `player_a_id` and
   `player_b_id` unchanged. Only `a_won` flips.
2. **Golden vectors:** a committed file of (pair, tournament, round → match_key, player_a) is checked on Linux and
   Windows CI.
3. **Row-order invariance:** shuffling input rows gives byte-identical canonical tables.
4. **Model antisymmetry (from P2):** `p(A,B) + p(B,A) = 1` within 1e-9 for every model.
5. **Balance:** sample-size-aware statistical checks (VALIDATION.md §5.1). These are a sanity check, not the guarantee.

---

## 5. Chronology and leakage-prevention policy

### 5.1 Why ordering is not enough
Source data gives the **tournament** start date only. `match_num` is documented as "sometimes arbitrary", and the
2026 file mixes counting directions. Several matches share a day with unknown start and finish times. Events
overlap (two-week Slams and Masters, Challengers, Sunday starts). Any single sequence number therefore *asserts*
an order the data cannot support. We do not use one as proof of chronology.

### 5.2 Time concepts (never conflated)

| Field | Meaning | Source today |
|---|---|---|
| `tournament_start_date` | Nominal start (`tourney_date`, "usually the Monday") | Sackmann |
| `tournament_end_bound` | Latest plausible final day | Derived: `event_class` + slack |
| `match_day_lo`, `match_day_hi` | Bounds on the calendar day the match was played | Derived: `round_day_bounds` |
| `match_date` | Actual play date | Null (no source) |
| `scheduled_start_utc` | Scheduled start | The Odds API `commence_time` for covered events only |
| `actual_start_utc`, `completed_at_utc` | Real start and finish | Null (no source) |
| `published_at_utc` | When a source first exposed the result | Null historically. Set by forward capture |
| `knowable_at_utc` | When we treat the result as known (derived, conservative) | §5.3 |
| `ingested_at_utc` | When we downloaded it | Exact |
| `prediction_cutoff_utc` | Information cutoff for a prediction | Per prediction |

### 5.3 Conservative derivation rules

Without a timezone we use the most conservative instant anywhere on Earth:
- *Earliest instant of day D* = `D 00:00` at UTC+14 = `(D−1) 10:00 UTC`.
- *Latest instant of day D* = `D 24:00` at UTC−12 = `(D+1) 12:00 UTC`.

Rules, applied in order of available precision:
1. `completed_at_utc` known → `knowable_at = completed_at + publication_lag` (default 0).
2. Else `scheduled_start_utc` known → `knowable_at = scheduled_start + max_duration(best_of)` + overnight-suspension
   slack. Defaults are `best_of=3`: 6 h and `best_of=5`: 9 h, both configurable. Suspensions beyond the slack are a
   documented residual risk.
3. Else → `knowable_at = latest instant of match_day_hi`.

**Default historical prediction cutoff:** `prediction_cutoff(m) = earliest instant of match_day_lo(m)`.
When `scheduled_start_utc` exists, `prediction_cutoff = scheduled_start − decision_lead` (default 60 min).
For betting backtests the cutoff is the **decision time** of the bet (VALIDATION.md §7).

**Rankings:** `knowable_at = latest instant of ranking_date`. A ranking dated on a tournament's Monday is therefore
*not* usable for that Monday's matches. The rank columns in source match rows (pre-event) are used only to QA our
rankings table.

**Missing or contradictory timestamps:** when a match lacks bounds (unknown round code or unknown event class),
`knowable_at = latest instant of tournament_end_bound`. Contradictions (e.g. a player's round-k+1 day bounds
ending before round k's begin) are quarantined.

### 5.4 Round day bounds
`reference/round_day_bounds.csv` holds offsets `(lo, hi)` in days from `tournament_start_date` per
`(event_class, round)`. Bounds are deliberately wide: qualifying starts before the nominal date, Sunday starts
exist, rain delays happen. Each row cites its rationale. Bounds are **validated** against `scheduled_start_utc`
wherever The Odds API covers a match. Any match falling outside its bounds is a QA failure that widens the bounds.

### 5.5 Information-set rule

For match *m* with cutoff *t*, a past result *r* belongs to the information set **I(m)** iff:

- **(K) Knowability:** `knowable_at(r) ≤ t`; **or**
- **(P) Certain precedence (the earlier-round exception):** *r* and *m* are in the same tournament edition, *r*
  involves a participant of *m*, and `round_ordinal(r) < round_ordinal(m)`.

  *Justification.*
  - In a knockout draw a player can enter round *k+1* only once their round-*k* match is resolved: a win, a walkover
    received, or (for a lucky loser) a qualifying loss.
  - The resolution of *r* therefore exists before *m* can begin. Every participant in *m* and every observer of the
    draw knows it at that point.
  - This holds even when both matches are on the same day, because play is sequential for the player.
  - It also covers qualifying → main draw, since qualifying shares the tournament edition and has lower ordinals.

  *Limits of the exception.*
  1. It admits only the **outcome fields** of *r* (`winner_id`, score, `outcome_type`). Post-match statistics of *r*
     (`match_stats`) still require (K), because their publication time is not implied by the draw structure.
  2. It does not admit results that do not involve a participant of *m*: opponents' opponents or other sections of
     the draw.
  3. Round-robin matches with equal ordinals have no mutual precedence.
  4. Data errors that break the draw logic (a player twice in one round, a later round with earlier day bounds) are
     quarantined by QA rather than trusted.

Nothing else is admissible. In particular:
- Same-day matches with unknown times **never** see each other.
- Another player's same-round result in the same event is excluded.
- Results from an overlapping event are excluded until knowable (K). The only exception is the player's own-path rule (P).

An optional rule (P′), off by default, would admit a player's own result from an overlapping *other* event. It may
be enabled only if a sensitivity study shows it is safe.

### 5.6 Stateful features under the rule

- **Batch semantics:** results become knowable in batches keyed by `knowable_at`. All results in one batch are
  applied to global state simultaneously, each update computed from pre-batch state. The result is therefore
  independent of the arbitrary order of matches within a day.
- **Two-layer state for Elo and rolling statistics:**
  - *Global state* includes every result with `knowable_at ≤ t`.
  - A per-prediction *overlay* applies the participants' own-path (P) results that are not yet knowable globally.
    It uses **outcome fields only**: stats-based features never use the overlay. The overlay is computed on a copy
    and never mutates global state.
  - When those results later become knowable, they enter global state through the normal batch path.
- The pipeline computes a prediction for *m* **before** any batch containing *m* is applied (predict-before-update).
- Every stateful feature (Elo, surface Elo, serve/return posteriors, form, fatigue, H2H) reads through
  `AsOfReader(cutoff, overlay_participants)`. Direct table access from feature code is blocked by an import-linter
  rule and a test.
- A **naive sequential** variant (order by start date then round ordinal) is kept *only* as a sensitivity comparison.
  The gap between the two variants measures leakage exposure.

### 5.7 Forbidden inputs (enforced by schema tags)
- Any field of match *m* tagged `post_match`: score, stats, minutes, outcome type, `a_won`.
- Closing or post-cutoff odds for *m*.
- Anything whose existence depends on the future: "was later charted", "career length", "number of career matches"
  (allowed only as a count *before* the cutoff), "eventually reached top 100".
- Dataset-wide aggregates computed over periods after the cutoff: scalers, encoders, country buckets, K-factors that
  depend on the dataset's last date, season-level surface mixes. All are fit inside training windows only.
  OPEN_SOURCE_AUDIT.md lists real examples of each.

### 5.8 Development / holdout separation in code
`configs/splits.yaml` defines `development_end = 2024-12-31` and `holdout_start = 2025-01-01` (D-05).
- **Phase 1** ingests and schema-validates 2025+ rows. QA on them is limited to integrity checks.
- `DataView(mode="development")` refuses (raises) any request that reaches `holdout_start`, and its default
  reads return development rows only. Stateful passes stop at the boundary.
- Only `mode="holdout"` with an explicit reason crosses the boundary. Each access is appended to
  `runs/holdout_access.log`.
- Model development, model selection and threshold optimisation use development-mode views only. The chronological
  folds inside 2005–2024 are defined in VALIDATION.md §2.2 and `configs/splits.yaml`.

---

## 6. Modeling architecture

Each component has its own task, baseline and acceptance metric (VALIDATION.md §3). Beating bookmakers is required
only for **betting** conclusions, never for keeping a component.

| Layer | Purpose | Notes |
|---|---|---|
| **M0 Market** | De-vigged probabilities: multiplicative, additive, power, Shin (2-way analytic) | Benchmark wherever contemporaneous odds exist. The method is chosen on development data only |
| **M1 Elo** | Overall + surface ratings, blend | Under §5.6 semantics. K = c/(n+o)^s (published FiveThirtyEight/Kovalchik form), tuned in-fold. Walkover: no update. Retirement policy: D-09. Inactivity decay and margin weighting are candidates evaluated in-fold |
| **M2 Serve/return** | Opponent-adjusted serve/return point rates, time-decayed, empirical-Bayes shrinkage, posterior uncertainty | Task: predict next-match serve-point win rate |
| **M3 Markov + MC** | Exact point→game→tiebreak→set→match DP. MC for joint market outcomes with parameter draws from the M2 posterior | Serve order carried across sets by game-count parity. Final-set rules per `scoring_rules.csv`. Analytic and MC agree within MC error. Optional anchoring to a headline probability, with **the same anchored parameters used for every market priced** |
| **M4 Calibrated ML** | Logistic (primary), LightGBM (challenger). Independent and market-anchored tracks | In-fold calibration (Platt or isotonic). Bootstrap/ensemble intervals |
| **Pricing** | Match winner, first set, total games, game handicap from one joint distribution | Lines outside the modelled support return "no price", never an extrapolation |
| **Final** | `p = w·p_model + (1−w)·p_market` in logit space, `w` fit in-fold | `w ≈ 0` means no evidence of an edge |

---

## 7. Licence lineage

- Each adapter declares a `licence_class` (DATA_SOURCES.md §1). Raw storage is namespaced by class.
- Every derived artifact (canonical table, feature set, rating table, model weights, calibration map, report,
  API response) carries `licence_lineage`, the set of classes it was derived from, recorded in its manifest.
- **Build profiles:**
  - `research` (default) permits `RESEARCH_NC`, `RESEARCH_PRIVATE`, `COMMERCIAL_LICENSED` and `OWN_COLLECTED`.
  - `commercial` refuses to load or serve any artifact whose lineage contains `RESEARCH_*`.
- `RESEARCH_PRIVATE` artifacts additionally cannot be exported, published or served by the API at all.
  They are local evaluation only.
- Public portfolio outputs (README figures, dashboard screenshots, published ratings) must satisfy the strictest
  licence in their lineage: attribution and NC, and ShareAlike if redistributed.
- Whether model weights or ratings legally count as "adapted material" is **unsettled**. We treat them as if they
  do (DATA_SOURCES.md §6.2).

---

## 8. Portfolio and staking (paper only)

- **Scenarios:** joint outcomes for all markets of one match are drawn from the **same** MC paths, so winner,
  handicap, first-set and totals correlations are captured. Matches are independent in v1. Futures/outrights are
  out of scope.
- **Objective:** maximise the sample-average log growth `E_s[log(1 + Σ x_i r_i(s))]` (cvxpy), scaled by a Kelly
  fraction λ. Benchmarks are flat stake, independent fractional Kelly, and the joint optimiser.
- **Inputs:** conservative probabilities (lower interval bound or market-shrunk) and odds after the slippage haircut.
- **Constraints:** per-bet, per-match, per-day and per-bookmaker caps; modelled max stake; drawdown governor.
- **No-bet is the default.** A bet is suppressed when any of these holds:
  - the edge after costs is below threshold;
  - the model–market gap is implausibly large;
  - the odds fail QA;
  - the identity match has low confidence;
  - features are missing;
  - the snapshot is stale;
  - the interval straddles the market;
  - the market is not covered by validated pricing.

---

## 9. Serving and operations

- **API (FastAPI, read-only):** `/health`, `/players/{id}`, `/ratings?as_of=`, `/predict`, `/price`, `/runs/{id}`.
  It never exposes `RESEARCH_PRIVATE` lineage. There is no bet endpoint.
- **Dashboard (Streamlit):** ratings, pricer, reliability plots, model-vs-baseline tables, paper ledger with CLV,
  QA report, attribution.
- **Docker:** one image. Data is mounted read-only and never baked in.
- **CI:** ruff, ruff format check, mypy, pytest (synthetic fixtures), Linux + Windows matrix, a `git ls-files`
  guard against data/secrets, and a no-network test marker.
- **Reproducibility:** `uv.lock`, seeded RNG (`numpy.random.Generator(PCG64(seed))`), and run manifests (git SHA,
  dirty flag, config, input manifests, package versions, licence lineage). Rebuilds have identical canonical
  content hashes. Byte-identical Parquet files are a goal, not a gate (D-12).
