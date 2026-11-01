# QuantEdge AI — Decision Log

Status values: **Accepted** (owner directive or approved), **Proposed** (recommended default; becomes Accepted on
approval), **Open** (needs owner input). "Blocks" names the earliest phase that cannot start without the decision.

Last updated: 2026-11-01 (Phase 1 approval).

---

## Accepted

| ID | Decision | Source |
|---|---|---|
| D-01 | No automated real-money betting. No bet-placement code or endpoints, ever. Paper trading only | Owner, Phase 0 brief |
| D-02 | Research data and commercially licensed data are separated by licence class at adapter, storage, artifact and build-profile level. Research-derived artifacts (including model weights and ratings) are never assumed commercially usable | Owner, Phase 0.5 Part A.1 |
| D-03a | No permission request will be sent to tennis-data.co.uk. Its contact page states such requests "will be ignored" | Owner, Phase 0.5 Part A.1; site terms |
| D-16 | **Build the core; adapt ideas, not code.** `shin` (MIT) is allowed as a test-only oracle. No third-party code is copied | Owner approval 2026-11-01 |
| D-05 | Partitions: burn-in ≤ 2004-12-31; development 2005-01-01 → 2024-12-31, with chronological inner folds (test years 2013–2022) and a late check (2023–24, ≤ 3 logged looks); **sealed holdout 2025-01-01 → research-data end**; prospective = own forward capture. Phase 1 may ingest and schema-validate 2025+ rows, but development, selection and threshold optimisation must not access them | Owner approval 2026-11-01 (folds added on owner request) |
| D-10 | Pin **Python 3.12** via uv | Owner approval 2026-11-01 |
| D-11 | P1 ingests `atp_matches_YYYY` + `atp_matches_qual_chall_YYYY` + `atp_players` + `atp_rankings_*`. Not futures, doubles or amateur | Owner approval 2026-11-01 |
| D-12 | Determinism = identical canonical content hash per table. Byte-identical Parquet is a goal, not a gate | Owner approval 2026-11-01 |
| D-13 | Chronology: knowability rule (K) + earlier-round exception (P, outcome fields only) + conservative UTC+14/UTC−12 day-instant bounds. P′ off | Owner approval 2026-11-01 |
| D-14 | Identity and orientation, **as amended**: <br>• canonical player IDs `sackmann:<int>`; <br>• `match_key` over tournament ID, season, draw category, round and the unordered canonical pair; <br>• deterministic collision resolution: exact-duplicate removal, then a `match_num` discriminator only when all colliding rows have distinct `match_num`, else quarantine; <br>• orientation bit from SHA-256 of the key (`qe.orient.v1`). <br>The winner never enters any identifier or the orientation. `match_num` is an ordering hint only | Owner approval 2026-11-01, subject to the corrections now in ARCHITECTURE.md §4 |
| D-17 | Fetch from `Aneeshers/tennis-sackmann-archive` at full commit `83733587353df8a41f2fd4f516147d5aa83f5a8d`. Verify git blob SHA-1s against the tree at that commit and record our own SHA-256 manifest | Owner approval 2026-11-01 |

---

## Blocking later phases

| ID | Question | Recommended default | Blocks |
|---|---|---|---|
| D-03b | Use of tennis-data.co.uk | Manual download. Private, untimed **evaluation benchmark only**: not a training feature, not a calibration target. Never committed or published at row level. Tier C results kept **out of the public portfolio** by default | P3 |
| D-06 | The Odds API purchase | After P2: one month on the 20K plan (verified $30/mo) for a **coverage probe**. Check which books and markets exist for ATP keys, and whether totals/handicaps exist historically. Then decide on a historical pull plus forward capture | P3 |
| D-09 | Retirement/walkover handling in ratings | Walkovers: no update. Retirements: no update by default, with "update with reduced K if ≥ 1 set completed" evaluated in-fold. Defaults/abandoned: no update | P2 |
| D-18 | Round day bounds values | P1 authors conservative bounds with written rationale. P3 validates them against Odds API `commence_time` and widens them where violated | P2 (used by Elo) |
| D-19 | Scoring-format table | Each final-set rule row cites a primary source (tournament or ATP rulebook). Unsourced rows block P4 pricing for that event | P4 |
| D-20 | Execution-model defaults | Latency 60 s; slippage grid {0, 1, 2, 3}% of odds; max stake grid. Always reported as unverified sensitivity | P6 |
| D-21 | Kelly fraction and caps | λ grid {0.10, 0.25}. Per-bet ≤ 1% bankroll, per-match ≤ 2%, per-day ≤ 5% (paper) | P7 |

---

## Open (owner input needed; non-blocking for P1)

| ID | Question | Recommended default |
|---|---|---|
| D-04 | Results source after the archive ends (~Roland Garros 2026) | Defer. Options: (a) TML as an optional research adapter; (b) own result capture for odds-covered events, which pairs naturally with forward odds capture; (c) commercial feed. Prefer (b) for prospective paper trading |
| D-07 | Betfair historic data | Excluded. Requires a Betfair.com account, unavailable in unsupported countries. Revisit only if the owner has lawful access |
| D-08 | Commercial ambition | Treat QuantEdge as a non-commercial research and portfolio project. Any commercial step requires a licensed results/stats feed, retraining from scratch, Odds API terms review and legal advice. No commercial claims in the public README |
| D-15 | Public-repo content | Public code, docs, synthetic fixtures and committed `reference/` tables. No data, no row-level outputs, no tennis-data-derived figures. Attribution block in README |

---

## Superseded

| ID | Old | Replaced by | Reason |
|---|---|---|---|
| P0-D2 | "Email tennis-data maintainer about model training" | D-03a/b | Site explicitly refuses such requests |
| P0-ordering | Global `event_seq` as chronology | D-13 | An ordering is not evidence of knowability |
| P0-orientation | "A-win rate 50% ± 1pp" acceptance | VALIDATION.md §5.1 | Not sample-size-aware. Structural guarantees take priority |
| P0-gating | "Each layer must beat the previous one / the market" | VALIDATION.md §3 | Component-specific baselines. Market is a benchmark, required only for betting conclusions |
