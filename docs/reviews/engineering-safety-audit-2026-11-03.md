# Engineering and safety audit before further V2 acquisition (2026-11-03)

**Scope:**
- Offline only. **0 Odds API calls, 0 credits.**
- No outcomes, late-check or holdout access; no `pilot-compare`.
- Frozen artifacts verified and unchanged: V1 `e3d3f642…da21`, V2 `ad319ba7…e70c`, calendar `2db8ee7b…d4a1`,
  `v2_draft.py` `ba505b20…02a7`, `stage.py` `7c7b692b…c512`.
- The raw store, every stage file (manifests, ledgers, tranche plans, gate, the week-1 lock) and the week-2 manifest
  are byte-identical before and after (43 files re-hashed).

**State verified:**
- 29 captures in the store.
- The US Open 2021 week-1 stage is locked (7 captures, 70 credits).
- The week-2 manifest (`v2a2-uso-2021-wk2`, 7 requests, 70 credits, strict window) has no ledger, tranche plan or lock.

## 1. Findings (severity-ranked, each with reproducible evidence)

Evidence was produced offline: dry runs, a synthetic temporary store and code inspection. Fixes are in §2.

| # | Severity | Class | Finding | Evidence |
|---|---|---|---|---|
| F1 | High | MISSING SAFETY CONTROL | **No programme-wide V2 credit accounting.** `run_stage` caps spend per stage ledger (`stage.py: run_stage`, `ceiling = min(planned_credits, …)`); nothing sums stages against the frozen 1,430 ceiling | A dry run of `stage-run --name v2a2-uso-2021-wk2` counted 10 → 70 from zero, ignoring week 1's 70. Any number of ≤ 130-credit manifests could exceed 1,430 |
| F2 | High | MISSING SAFETY CONTROL / SCIENTIFIC RISK | **`capture-historical` bypassed every V2 control.** Any paid snapshot could be bought inside a V2 edition window, with no ledger. A snapshot in `(T_d − 10, T_d]` would then become a V2 as-of snapshot, unbudgeted | `commands.py: capture_historical_cmd` has no calendar, ledger or ceiling check |
| F3 | Medium | MISSING SAFETY CONTROL | **No runtime check of frozen fingerprints** (protocol, calendar, implementation) before a paid call; they were pinned only by a CI test | No reference to `market_benchmark_v2.yaml`, `calendar_sha256` or `files_sha256` anywhere in `src/` |
| F4 | Medium | MISSING SAFETY CONTROL | **No cross-stage overlap check.** Two stages can claim the same tournament day. The store check only skips a re-buy *after* purchase, and only for an identical returned snapshot | Synthetic: manifests `a` and `b` for 2021-09-06 both report `would_request` |
| F5 | Medium | MISSING SAFETY CONTROL | **The runner does not verify a manifest against the frozen calendar.** Only `v2_audit` and `check_tranche` do, after purchase | Synthetic: a manifest edited to 20:00Z passes `run_stage` (`would_request`) |
| F6 | Medium | MISSING SAFETY CONTROL | **Other stages' locks, ledgers and tranche plans are not checked** before a paid call. A tampered lock or ledger elsewhere goes unnoticed | `run_stage` checks only its own directory's lock |
| F7 | Low | MISSING SAFETY CONTROL | **Paid V1 stage manifests could still be executed** although V1 acquisition is suspended (D-34) | `stage-run --execute` accepted any manifest |
| F8 | — | NOT AN ISSUE | Within-stage controls behave as specified: strict window, cumulative ledger cap, `max_retries = 0`, quota reconciliation, write-once lock, tranche gate, idempotent restart, SHA-256 re-verification of every capture body on read | Existing tests (`test_v2_draft.py`, `test_odds_stage.py`) |
| F9 | High (for the confirmatory phase) | FUTURE FEATURE / BLOCKER | **No V2 confirmatory comparison engine.** `pilot-compare` (`odds/pilot.py: run_pilot`, `pilot_population`) is V1-specific: V1 protocol model and V1 decision anchor. V2 needs its own runner on locked editions | Code inspection |
| F10 | Medium | SCIENTIFIC RISK | **Late-check exposure for future ML.** The default development `DataView` ends 2024-12-31, which includes the 2023–24 late check. Look counting lives in gitignored `runs/late_check_looks.json` (deletable). The holdout is code-enforced (`HoldoutAccessError`) | `core/dataview.py`, `experiments/registry.py` |
| F11 | Low | SCIENTIFIC RISK | **Real-data invariants are not checked in CI.** 12 `local` tests (AT-19 reproducibility, the real-state guard) are deselected by `-m "not local"` | `.github/workflows/ci.yml` |
| F12 | Low | SCIENTIFIC RISK | Pinnacle adequacy is likely to fail on the full population (54 % in week 1); the frozen fallback applies | Feasibility review §6 |
| F13 | — | NOT AN ISSUE | Licensing: the hygiene guard (CI and pre-commit) forbids tracked `data/` and `runs/`, binary data types and secrets. Licence classes propagate (`RESEARCH_NC`, `COMMERCIAL_LICENSED`), and licensed odds stay under gitignored `data/` | `devtools/hygiene.py`; hygiene passes on all tracked files |

## 2. Fixes (minimal, outside the frozen implementation)

`stage.py` and `v2_draft.py` are pinned by the frozen protocol and were **not edited**. The fixes are a new,
separately reviewable wrapper plus CLI wiring. No estimand, population rule, threshold, prediction, decision or result
changed.

**`src/quantedge/odds/v2_guard.py` (new):**
- `verify_frozen_v2` (F3) re-verifies the V2 SHA-256, `status: FROZEN`, the calendar SHA-256 and the pinned
  implementation hashes.
- `programme_state` (F1, F4, F5, F6) accounts every V2 stage. It fails closed on:
  - a manifest that does not match the frozen calendar;
  - a ledger entry not matching its stored capture (ID and charged credits);
  - an unreconciled cumulative total;
  - a stale tranche plan;
  - a lock that does not match its manifest, ledger or capture list;
  - a day paid twice;
  - any historical capture stored on or after the freeze date that no ledger accounts for (F2);
  - spending already above the ceiling.
  - Legacy and V1 manifests are listed as skipped, not counted.
- `preflight` refuses a paid call if a target day is claimed or paid by another stage, or if spent plus this call's
  maximum exceeds the frozen `programme_ceiling_credits` (1,430). Already-paid requests cost nothing.
- `guarded_stage_run` and `guarded_tranche_run` run the preflight, then delegate to the frozen runners.
- `v2_window_conflict` identifies an edition window (first day − 2 … last day + 2).

**`src/quantedge/odds/commands.py`:**
- `stage-run` runs V2 manifests through the guard, preflight first and before any client exists, and refuses paid V1
  execution (F7).
- `v2-tranche-run` is guarded the same way.
- `capture-historical` is refused inside any frozen V2 window (F2).

**Real programme state through the guard:** spent **70** (week 1, locked). Week 2: at most **70**, with **1,290**
remaining afterwards. The guard also re-recognises the locked week 1 (a tranche-2 attempt stops: "locked"), refuses
paid V1 execution and refuses a `capture-historical` at 2021-09-06T13:59Z.

## 3. Regression tests

**`tests/unit/test_v2_guard.py`: 20 tests, synthetic, scripted transport.** Each unsafe case is shown to be refused
before any request (the transport saw zero URLs, or the client was never constructed):
- frozen protocol, calendar or either implementation file changed (4 cases);
- cross-stage ceiling (spent 20 + 20 > 30);
- refusal before any request, for stages and for tranches;
- overlapping day;
- an off-calendar manifest anywhere;
- a locked stage's ledger or manifest changed (2);
- ledger not matching the store;
- an unaccounted post-freeze capture (pre-freeze design captures allowed);
- a resume costs only the unpaid requests;
- window bounds;
- CLI: `capture-historical` refused in-window and not constructed out-of-window; paid V1 execution refused;
- **static:** no paid call site in `src/` beyond the guarded, allowed ones. The frozen modules cannot be edited, so
  new bypasses are caught.

**`tests/local/test_v2_guard_real.py`: 1 local test** pinning the real programme state above.

## 4. Validation (exact)

| Check | Result |
|---|---|
| `ruff check .` / `ruff format --check .` | pass / 131 files formatted |
| `mypy --strict src/quantedge` | no issues (59 files) |
| Full `pytest`, including local real-data tests (after the data rebuild for AT-19) | **391 passed, 0 failed** (390 + the static call-site test) |
| CI selection `pytest -m "not local"` | **379 passed**, 12 deselected |
| Leakage + guard + fingerprint subsets | pass |
| Repository hygiene (all tracked files, and changed files) | pass |
| Integrity (raw store, stage files, frozen fingerprints) | unchanged |

GitHub Actions is verified after the push, from the actual run result.

## 5. Remaining risks and blockers

1. **Library-level bypass.** Programmatic `run_stage`/`run_tranche` calls skip the guard. They cannot be edited
   (frozen), so this is mitigated by the static call-site test and documented here.
2. **V2 confirmatory engine missing (F9):** a blocker for any model-vs-market claim, not for acquisition. It must be
   built and reviewed before analysis, without changing the frozen estimand.
3. **Late-check discipline for ML (F10).** Recommended:
   - any new training code reads canonical rows with `end = inner_end`;
   - late-check scoring goes only through the registry's capped looks;
   - the look counter should be backed up outside `runs/`.
4. **Budget:** 1,290 credits remain after week 2 for 129 planned days, still with zero slack (contingency not
   authorized).
5. **CI coverage (F11):** data-dependent invariants remain local-only by licence. Keep running the local suite before
   any paid call.

## 6. Next-stage ML plan (design only; zero odds credits; not implemented)

Everything below uses the existing licensed research archive and the Phase 1/2 infrastructure. It needs **no odds
credits**.

1. **Features, point-in-time:** each feature is computed from matches with `knowable_at ≤ prediction_cutoff`, using
   batch-simultaneous replay as in Phase 2.
   - **Ratings:** overall and surface Elo (existing); Elo uncertainty (match count, recency); Elo difference and
     surface-specific gaps.
   - **Player statistics:** rolling, exponentially decayed service and return quality (1st-serve-in %, 1st/2nd-serve
     points won, break points saved/converted, return points won), shrunk towards surface or tour means by sample
     size.
   - **Surface form:** surface win rates against opponent-strength-adjusted expectations (Elo-residual form).
   - **Form and opponent strength:** last-N results weighted by opponent Elo.
   - **Workload:** matches, sets and minutes in the last 7/14/30 days; days since the last match; inactivity flags;
     travel or continent change where derivable.
   - **Context:** tournament level, round, best-of-3/5, ranking and age where knowable.
2. **Training protocol:**
   - strictly chronological walk-forward folds (the existing inner folds, test years 2013–2022);
   - nested time-series tuning inside each training window;
   - a frozen model version per pre-registration;
   - no late-check or holdout use until the version is frozen, and then only counted looks.
3. **Model:**
   - LightGBM (binary log-loss) on oriented match pairs, with deterministic orientation (D-14);
   - monotone constraints on Elo difference;
   - calibration (Platt or isotonic) fitted on each fold's calibration year only.
4. **Evaluation against Elo, on identical matches:**
   - paired per-match log-loss and Brier differences;
   - ISO-week block bootstrap CIs;
   - reliability diagrams and calibration slope/intercept;
   - sensitivity by surface, level and round;
   - a pre-registered primary contrast and a single confirmatory late-check look.
5. **Overfitting and leakage controls:**
   - feature-timestamp unit tests (every feature ≤ cutoff);
   - permutation and label-shuffle sanity checks;
   - an ablation ladder (Elo → +serve/return → +form → +fatigue);
   - pinned fingerprints for feature code and configuration;
   - a leakage test suite extending `tests/leakage/`.
6. **Later, separately approved:**
   - comparison against the market under the frozen V2 (start-of-day estimand) once its confirmatory engine exists
     and all editions are locked;
   - paper-trading evaluation only after a credible probability-quality result, with fixed staking rules and
     execution caveats (exchange commission, no executable-price claims);
   - never real-money betting.

## 7. Verdict

**READY for further V2 historical acquisition, on the engineering-safety side**, once these fixes are committed and
CI is verified. The programme ceiling, frozen fingerprints, calendar, overlaps, locks, ledgers and unaccounted
captures are now enforced fail-closed before any paid request.

Each acquisition still requires explicit owner approval: week 2 would be **70 credits, leaving 1,290**. The V2
confirmatory analysis is **NOT READY**: its engine is missing (F9).
