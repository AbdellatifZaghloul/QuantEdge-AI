# Phase 3B.1 review — pairwise contrast populations in the pre-registered pilot

Date: 2026-11-02. Base commit: `d95ccc6` (Phase 3B).
- No real odds, no credentials, no network calls to the provider, zero credits spent.
- Phase 2 results unchanged; no access to 2023–24 late-check years or the 2025+ holdout.
- The frozen protocol `configs/preregistration/market_benchmark_v1.yaml` is **unchanged**. SHA-256 still
  `e3d3f642d3dd85064136c8bf8d9fc984887d1beb84d68ad9960c92ec2467da21`, verified after recovery.

## 1. Recovery after the PC shutdown

- `git status` showed exactly the three in-progress Phase 3B.1 files:
  - modified: `src/quantedge/odds/pilot.py`, `tests/integration/test_odds_pilot.py`;
  - new: `tests/integration/test_odds_pilot_contrasts.py`.
- No other file differs from `d95ccc6`. The diff was reviewed and preserved as-is; nothing was restarted.
- **The interrupted background command** ran ruff check, ruff format check, then `pytest` piped to `tail`.
  - Its log shows ruff passing ("All checks passed!", "96 files already formatted"), then a pytest progress line
    cut off at about 106 tests, **with no pytest summary line**, and "exited with code 4".
  - pytest's own exit code 4 means a usage error, which pytest reports *before* running tests. Tests were clearly
    running, so code 4 did not come from a normally completed pytest run.
  - The most consistent explanation is that the process was terminated mid-run (the run had already exceeded the
    10-minute foreground limit and the PC then shut down). The exact signal cannot be recovered from the log.
  - The single `F` visible in that partial log was reproduced and explained: it is **AT-19** (below), not a defect.
- **AT-19 failure (expected, by design):**
  - Editing `pilot.py` changes the package code fingerprint, so a fresh real-data build gets a new build ID
    (`b471f06f82565e61`) that differs from the stored build (`cca7ad5d59054438`).
  - The data were rebuilt with the current code. **All seven canonical content hashes are identical.**
  - The old build directory was replaced, QA and the Gate 0 audit were regenerated, and AT-19 then passed.
  - The test was not changed.

## 2. The bug

Two defects in `run_pilot`, both in how the pre-registered contrasts were scored.

1. **Over-restrictive population.**
   - All four columns (`market_primary`, `elo`, `rank`, `market_consensus`) were passed together to
     `compare_on_identical_population`, which keeps only rows where *every* column is present.
   - The consensus needs at least two bookmakers, so any match with a valid primary-bookmaker quote but fewer than
     two bookmakers was silently dropped from *all* contrasts, including Elo vs primary.
   - Reproduced on synthetic data with one bookmaker: the old code scored **0 of 4** matches.
2. **Missing contrast.**
   - Every difference was computed against the first column (`market_primary`), so the pre-registered contrast
     **Elo − consensus was never produced**.
   - An unregistered "consensus − primary" difference appeared instead. Reproduced with two bookmakers.

The staleness and de-vig sensitivities inherited the same four-column restriction.

## 3. Consistency with the frozen protocol

- The YAML pre-registers `metrics.contrasts` as three pairs "each as (model − market reference)". The protocol
  document states that each contrast is scored only where its compared columns exist.
- The old code deviated from both: it imposed a four-column population and did not score one registered contrast.
- The correction **implements the frozen protocol as written**. No protocol change is needed, and none was made.
- No real odds or outcomes were used to decide this. The issue and its tests are purely synthetic.

## 4. The correction (`src/quantedge/odds/pilot.py`)

**Pairwise scoring.**
- Each contrast in `protocol.metrics.contrasts` is scored by `pairwise(model, reference)` on exactly the rows where
  *both* of its columns exist.
- Paired log-loss and Brier differences are model − reference, with the pre-registered block bootstrap.

**Reported per contrast:**
- `n_population`, `n_scored`;
- `excluded_missing_model`, `excluded_missing_reference`, `excluded_missing_both`;
- model and reference metrics;
- `log_loss_diff`, `brier_diff`.

**Other changes:**
- The **four-column common population** is kept as `supplementary_common_population`, labelled
  "SUPPLEMENTARY, not pre-registered as primary".
- **Staleness sensitivity (15/60 min)** uses the same pairwise contrasts.
- **De-vig sensitivity** scores Elo vs the primary bookmaker under each secondary method (power, Shin), each
  pairwise.
- `sample_sizes.scored_per_contrast` replaces the single "identical population" count.

**Unchanged:**
- population construction;
- the strict point-in-time selector;
- outcome-blind reference choice and guards (still applied before outcomes are joined);
- market probability orientation;
- de-vig maths;
- the bootstrap implementation.

## 5. Regression tests (`tests/integration/test_odds_pilot_contrasts.py`, all synthetic)

| Test | Case | Old code | Now |
|---|---|---|---|
| `test_contrasts_are_the_preregistered_pairs` | Contrast set equals the three registered pairs | Failed: Elo − consensus missing | Pass |
| `test_only_one_bookmaker_available` | One bookmaker: primary contrasts use all 4 matches; consensus 0, with 4 recorded as missing reference | Failed (0 scored) | Pass |
| `test_primary_available_consensus_unavailable_for_some` | Primary on 4, consensus on 2; supplementary common population 2 | Failed | Pass |
| `test_consensus_available_primary_unavailable_for_some` | Pinnacle 75 % → outcome-blind fallback; consensus present where the preferred book is not | Failed | Pass |
| `test_partial_coverage_reports_exact_exclusions` | Several books, partial coverage: exact `n_scored` and exclusion counts | Failed | Pass |
| `test_pairwise_values_match_a_direct_computation` | Pairwise log-loss and Brier differences equal a hand computation using proportional de-vig and correct A/B orientation | Failed | Pass |

The existing end-to-end pilot test was updated to the corrected report structure, keeping every original assertion:
four scored matches, the four-column view, Brier differences present, and the staleness sensitivity. It also
checks the de-vig sensitivity keys.

## 6. Validation (run separately, 2026-11-02, Windows)

| # | Command | Result | Exit |
|---|---|---|---|
| — | Targeted: `pytest tests/integration/test_odds_pilot.py tests/integration/test_odds_pilot_contrasts.py` | 12 passed | 0 |
| 1 | `uv run ruff check .` | All checks passed | 0 |
| 2 | `uv run ruff format --check .` | 109 files already formatted | 0 |
| 3 | `uv run mypy --strict src/quantedge` | No issues in 56 source files | 0 |
| 4 | `uv run pytest -m "not local" -q` | **254 passed**, 11 deselected (36 s) | 0 |
| — | `uv run pytest -m local -q` (diagnosis, before rebuild) | 10 passed, 1 failed (AT-19, stale build ID) | 1 |
| 5 | `uv run pytest -q` (after rebuild) | **265 passed** (81.5 s) | 0 |
| — | Repository hygiene (tracked + changed files), pre-commit hooks | Clean / all passed | 0 |

The full suite takes about 1.5 minutes. The slowest test is AT-19 at 44 s, so the earlier more-than-10-minute run
was not caused by the test suite.

## 7. Scientific impact

- No real comparison has been run yet, so **no reported result changes**.
- Had the bug reached the confirmatory run, the primary contrasts would have been scored on a smaller,
  consensus-dependent subset. That subset is biased toward events with broad bookmaker coverage, which typically
  means higher-profile matches. The Elo − consensus contrast would also have been missing.
- The correction restores the pre-registered design before any odds are seen.

## 8. Remaining limitations

- Pairwise populations differ between contrasts, so contrasts are not directly comparable to each other. The
  supplementary common population exists for that purpose and is labelled as non-primary.
- There are only 10 bootstrap blocks in the pilot window (one per Slam edition).
- The pilot remains Grand Slam only; probe outcomes (bookmaker presence, Pinnacle coverage) are still unknown.
- Every real-data run after a code change needs a rebuild for AT-19. This is by design: build IDs include the code
  fingerprint.

## 9. Readiness

- The code is ready for the owner-approved 40-credit probe (approvals A1–A4 in `phase-3b.md` §6).
- The probe uses only the adapter and store, never `run_pilot`, and joins no outcomes.
- **Not yet committed:** these changes await review before commit and push.
