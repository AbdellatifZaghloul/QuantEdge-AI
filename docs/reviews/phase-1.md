# Phase 1 review — data foundation

Date: 2026-11-01. Scope: ROADMAP.md Phase 1 (AT-01…AT-29). No ratings, models, odds or betting code.

All real-data figures below are **aggregate counts** from local build `3d270f54281bb2b9` of the pinned
snapshot. No licensed rows are reproduced here. Synthetic-fixture results and real-data results are
reported separately.

## 1. What was built

| Component | Module |
|---|---|
| Pinned fetch + verification (git blob SHA-1 against the tree at the full commit, our SHA-256 manifest, offline re-verify, IPv4-first connector) | `quantedge.adapters.research.sackmann_archive` |
| Reference tables (round ordinals, levels, event classes, day bounds, score tokens) with rationale | `quantedge/reference/*.csv`, `quantedge.canonical.reference` |
| Score parser (RET/W/O/DEF/ABD variants, tiebreaks incl. un-notated 9-8, match tiebreaks, short sets) | `quantedge.canonical.score` |
| Identity, collision resolution, outcome-independent orientation | `quantedge.core.ids`, `quantedge.canonical.build` |
| Canonical schemas (Pandera) with `post_match` and `locator` tags | `quantedge.canonical.schemas` |
| Chronology fields, conservative day instants | `quantedge.core.chronology` |
| Information-set rule (K + P) and replay scaffold | `quantedge.core.asof` |
| Development/holdout guard, chronological folds | `quantedge.core.dataview`, `quantedge.core.splits` |
| Licence lineage and build profiles | `quantedge.core.lineage` |
| QA report (JSON + Markdown), orientation statistics | `quantedge.quality` |
| CLI `quantedge data fetch / verify / build / qa` | `quantedge.cli` |
| Repo hygiene + secret scan (CI and pre-commit) | `quantedge.devtools.hygiene` |

## 2. Real snapshot (local only)

- Source: `Aneeshers/tennis-sackmann-archive` at `83733587353df8a41f2fd4f516147d5aa83f5a8d`.
- 116 files, 163,210,413 bytes, all verified against their pinned git blobs.
- Tournament start dates run from 1967-12-28 to **2026-06-01**. Phase 0.5 had estimated 2026-05-25 from sampling;
  the full ingest shows Challenger events through 2026-06-01. Rankings run to 2026-06-08.

**Tables:**

| Table | Rows |
|---|---|
| matches | 439,853 |
| match_entry | 879,706 |
| match_stats | 470,768 |
| players | 66,912 |
| rankings | 3,419,216 |
| tournaments | 14,345 |
| rejected_rows | 1,386 |

**Reconciliation:** 3,927,367 rows in = 3,925,981 canonical + 1,386 rejected. The equation holds exactly for every
one of the 116 files.

**Rejections:**

| Reason | Count | Files |
|---|---|---|
| `RANKING_CONFLICT` | 1,198 | rankings: 599 player/date pairs with differing rank or points, all rows quarantined |
| `DUPLICATE_ROW` | 181 | rankings: exact duplicates, one copy kept |
| `SAME_PLAYER` | 7 | matches |

There are no `UNKNOWN_ROUND`, `INVALID_*` or `KEY_COLLISION` rejections.

**Identity:**
- 35 collision groups (70 matches) were resolved by the `match_num` discriminator. None needed quarantine.
- No cross-file duplicates exist between tour and qualifying/Challenger files.
- 174 matches are flagged `precedence_conflict`: a player appears twice in one knockout round of one event.
  These are excluded from the earlier-round rule.

**Scores (development period):**

| Score status | Count |
|---|---|
| ok | 416,669 |
| inconsistent | 1,468 |
| missing | 181 |
| uncertain | 14 |
| malformed | 3 |

- The inconsistent scores are mostly source contradictions, chiefly `best_of` disagreeing with the set count, and
  are concentrated in the 1970s.
- Outcome types: completed 403,724; retired 10,552; walkover 2,124; default 232; abandoned 38; unknown 1,665.

**Other integrity checks:**
- **Stats consistency:** all rules pass except `second_won ≤ second serves`, which fails for 5 player-matches.
- **Unparseable stat values:** 2. **Out-of-range heights:** 7 (nulled).
- **Players:** none missing from the players table. Birth-date precision: day 48,171; year 102; unknown 18,639.
- **Rank cross-check:** 775,724 entries with a source rank. 172 have no ranking row on or before the event start.
  16,905 differ from the latest ranking-table value. Source ranks are pre-event, so they are used only for QA.
- **Overlapping events (nominal intervals):** 42,035 player-event entries, 2,849 players. Expected around two-week
  events; handled by the knowability rule.
- **Knockout-logic violations (development):** 93 player appearances after a knockout loss. Recorded for
  investigation and not used.

**Orientation checks** (development, completed matches, α = 0.001, Holm):

| Test | n | Rate | p | Holm p | Result |
|---|---|---|---|---|---|
| Global binomial (A won) | 403,724 | 0.50127 | 0.108 | 0.324 | pass (±0.00259 band) |
| Stratified G (year × level, 249 strata) | 403,724 | — | 0.860 | 0.861 | pass |
| A higher-ranked | 341,748 | 0.50068 | 0.430 | 0.861 | pass |

**Holdout (integrity only):**
- 21,518 matches in 530 tournaments with start date ≥ 2025-01-01.
- Score status: ok 21,500; missing 15; inconsistent 3.
- No outcome statistics were computed on holdout rows.

**Determinism:**
- Two real builds produced identical content hashes for all 7 tables.
- The local test AT-19 rebuilds again and compares.

## 3. Acceptance tests

Tests marked "local" run only on this machine with the real snapshot. CI runs the synthetic suite on
ubuntu-latest and windows-latest.

| ID | Evidence | Synthetic / CI | Real (local) |
|---|---|---|---|
| AT-01 | `uv sync --locked`, Python 3.12.16 | Windows ✔ locally; CI matrix configured | ✔ |
| AT-02 | ruff check, ruff format --check, mypy --strict (25 files) | ✔ | — |
| AT-03 | `test_fetch_and_network.py`, `local::test_at03…` | ✔ | ✔ (116 files) |
| AT-04 | Socket guard in `conftest.py`, `test_network_is_blocked` | ✔ | ✔ |
| AT-05 | `test_written_parquet_validates_against_schemas`, `local::test_at05…` | ✔ | ✔ |
| AT-06 | `test_every_input_row_is_accounted_for` (independent CSV count), `local::test_at06…` | ✔ | ✔ |
| AT-07 | `test_match_keys_unique_and_collisions_resolved` | ✔ | ✔ (schema uniqueness) |
| AT-08 | `test_winner_swap_invariance` (300 examples × 4–8 rows) | ✔ | — |
| AT-09 | `test_golden_vectors_reproduce` (64 vectors) | ✔ Windows; Linux in CI | — |
| AT-10 | `test_row_order_does_not_change_content` | ✔ | — |
| AT-11 | `test_no_winner_first_columns_downstream` | ✔ | ✔ (same schemas) |
| AT-12 | Mutation test (55% leak on 10k is detected), `local::test_at12…` | ✔ | ✔ |
| AT-13 | 50 hand-written cases + round-trip / totality properties | ✔ | ✔ (token inventory in QA) |
| AT-14 | `test_round_ordinals_and_precedence_conflicts`, `local::test_at14…` | ✔ | ✔ |
| AT-15 | `test_day_instants_explicit_values`, `test_chronology_fields` | ✔ | ✔ (schema) |
| AT-16 | `test_information_set.py` scenarios (a)–(f) | ✔ | — |
| AT-17 | `test_replay.py`: perturbation, truncation, order invariance, leaky-engine power check | ✔ | — |
| AT-18 | `test_dataview_seals_holdout`, `test_qa_report_holdout_section_is_integrity_only` | ✔ | ✔ |
| AT-19 | `test_rebuild_is_deterministic`, `local::test_at19…` | ✔ | ✔ |
| AT-20 | `test_licence_lineage_blocks_commercial_profile` | ✔ | ✔ |
| AT-21 | `test_hygiene.py`, CI hygiene step | ✔ | — |
| AT-22 | `local::test_at12_at22_at23…` (max start date 2026-06-01) | — | ✔ |
| AT-23 | QA report sections present with real counts | ✔ | ✔ |
| AT-24 | `test_every_key_field_separates`, property `test_key_changes…` | ✔ | — |
| AT-25 | `test_match_keys_unique…`, `test_collision_resolution_independent_of_row_order` | ✔ | ✔ (35 groups) |
| AT-26 | `test_match_num_permutation_changes_only_the_hint` | ✔ | — |
| AT-27 | `test_at27_contract_on_synthetic_build`, `local::test_at27…` (30 sampled) | ✔ | ✔ |
| AT-28 | `test_splits_are_chronological_and_inside_development` | ✔ | — |
| AT-29 | `test_canonical_player_id_*`, `test_padded_and_zero_prefixed_ids_canonicalise` | ✔ | — |

Totals at the time of writing: 155 tests, all passing locally on Windows (148 synthetic + 7 local real-data).

## 4. Deviations and issues found during implementation

1. **Reference tables moved** to `src/quantedge/reference/` (package data) instead of a top-level `reference/`.
   Documentation is updated.
2. **Build ID includes a code fingerprint.** Before this, a parser change kept the same build ID, so two different
   outputs could share an ID. Every source-code change now yields a new ID.
3. **Parser fix:** un-notated 9-8 and 8-9 tiebreak sets (1970s format) were initially flagged inconsistent; 462 more
   development scores now parse correctly. Unfinished sets in retirements no longer count as sets won.
4. **`tzdata` dependency added:** Windows has no system IANA database, and polars needs it to return UTC datetimes.
5. **Local network:** this machine's IPv6 routing is broken, which stalled Python's sequential connect for about 84 s
   per request. The fetcher now tries IPv4 first, with a 10 s connect timeout per address.
6. **Tooling install:** uv was installed into an isolated user venv (`~/.local/uv-venv`), and uv downloaded a
   user-level managed CPython 3.12.16. No system-wide software was modified.

## 5. Known limitations (carried forward)

- `round_day_bounds.csv` values are conservative **heuristics**, not yet validated against real schedules (D-18, P3).
- Davis Cup date semantics are unverified. The class uses wide bounds.
- The 93 knockout-logic violations, 174 precedence conflicts and 1,468 inconsistent development scores are flagged
  but not resolved. P2 must decide inclusion policies (D-09).
- `match_stats` has no `minutes`-based or point-level checks beyond the listed consistency rules.
- The code licence for this repository is undecided (D-22).
