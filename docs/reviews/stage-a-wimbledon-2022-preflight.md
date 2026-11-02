# Stage A preflight — Wimbledon 2022 decision-time snapshots

Date: 2026-11-02. Status: **prepared, not executed.** The paid requests need explicit owner authorisation.

**This preflight:**
- Fully offline: sockets disabled for every real-data command. **0 network calls, 0 credits spent**, no
  `capture-historical` run.
- No outcomes joined. 2023–24 and 2025+ untouched.
- `market-benchmark-v1` unchanged (SHA-256 `e3d3f642…67da21`).
- The raw store is byte-identical before and after (4 log records, 4 bodies, all SHA-256 re-verified).
- No provider event IDs, player names or prices appear here. The manifest with event IDs exists only under the
  gitignored `data/processed/odds/the_odds_api/stages/stage-a-wimbledon-2022/`.

## 1. Discovery capture

| Item | Value |
|---|---|
| Capture | `cap1_d547a2721400551bc5ad7d03e371022a` (`tennis_atp_wimbledon`, h2h, `eu`) |
| Body SHA-256 | `3cc2608862a0618b43f238ba1fbd8983fb891dd1af285ba7a0991e28bd706b54`, verified on read |
| Snapshot | requested 12:00Z, returned **2022-06-28T11:55:00Z**; previous 11:45, next 12:05 |
| Grid used for planning | 10-min cadence at **:x5** (inferred from those neighbours) |
| Events in the snapshot | 48: 39 pre-match, 9 already started (in-play, never planned) |
| h2h `last_update` lag at the snapshot (min) | p50 4.7, p90 5.0, p99 11.9, max 19.1 |
| Provider quota after the probe | 19,960 (last recorded `x-requests-remaining`) |

Only the schedule fields of this snapshot (event ID, sport key, scheduled start) are used. No quote from it influences
the plan.

## 2. Request manifest (generated in code)

Command: `quantedge odds stage-plan` (offline). The manifest JSON's SHA-256 is `c019aca1…5221`; regenerating it
gives identical bytes.

**Rules:**
- decision = scheduled start − 60 min (frozen lead);
- each group is requested at its **earliest** decision, so the returned snapshot (≤ requested time) is never
  later than any decision it serves;
- an event joins a group only while decision − expected snapshot ≤ **15 min**;
- freshness (≤ 30 min, source `last_update` based) is enforced afterwards by the frozen selector, not assumed.

| # | `date` requested (UTC) | Expected snapshot | Events | Discovered later | Decision span | Max gap (min) | Credits | Cumulative |
|---|---|---|---|---|---|---|---|---|
| 01 | 2022-06-28T11:00:00Z | 10:55 | 4 | 4 | 11:00–11:10 | 15 | 10 | 10 |
| 02 | 2022-06-28T12:30:00Z | 12:25 | 6 | 0 | 12:30–12:40 | 15 | 10 | 20 |
| 03 | 2022-06-28T12:43:00Z | 12:35 | 1 | 0 | 12:43 | 8 | 10 | 30 |
| 04 | 2022-06-28T13:00:00Z | 12:55 | 4 | 0 | 13:00–13:10 | 15 | 10 | 40 |
| 05 | 2022-06-28T13:30:00Z | 13:25 | 3 | 0 | 13:30–13:40 | 15 | 10 | 50 |
| 06 | 2022-06-28T14:25:00Z | 14:25 | 4 | 0 | 14:25–14:40 | 15 | 10 | 60 |
| 07 | 2022-06-28T14:47:00Z | 14:45 | 4 | 0 | 14:47–15:00 | 15 | 10 | 70 |
| 08 | 2022-06-28T16:47:00Z | 16:45 | 2 | 0 | 16:47–17:00 | 15 | 10 | 80 |
| 09 | 2022-06-29T09:00:00Z | 08:55 | 11 | 0 | 09:00 | 5 | 10 | 90 |
| | | | **39** | **4** | | | | **90** |

- **Gap per event** (decision − snapshot): median 5 min.
  - Distribution: 0 min ×1, 2 ×2, 5 ×18, 6 ×2, 8 ×1, 10 ×1, 11 ×4, 15 ×10.
- **Expected quote age at decision** = gap + market lag:
  - typical ≈ 5 + 4.7 ≈ 10 min;
  - worst planned ≈ 15 + 11.9 (p99 lag) ≈ 27 min;
  - markets lagging > 15 min on a 15-min-gap event will be `stale`. That is measured, not assumed away.

## 3. Is 12 × 10 credits enough?

| Policy | Requests | Credits | Max gap | Verdict |
|---|---|---|---|---|
| Strict per-grid (gap < 10 min) | 15 | 150 | 9 min | **Exceeds the 130 ceiling**, not possible without new approval |
| Audit's "12 clusters" (inclusive 10-min decision spans) | 12 | 120 | 15 min | Fits, but uses 3 more calls than needed for the same gap |
| **Chosen: gap ≤ 15 min, request at earliest decision** | **9** | **90** | **15 min** | **Fits; same worst-case gap as the 12-cluster plan** |
| Gap ≤ 20 min | 8 | 80 | 18 min | Rejected: less freshness headroom for 10 credits saved |

- **Answer:** yes, 12 × 10 would suffice, but the exact plan needs **9 × 10 = 90**.
- **Ceilings:**
  - the runner's cumulative cap is the planned 90, not 130;
  - the 130 ceiling stays absolute;
  - the 40 credits of headroom (including the 10 contingency) are **never used automatically**.
- **Grouping is an acquisition choice, not a protocol change.** Lead, anchor, freshness, the selector and the
  reference rule are all untouched. A tighter grouping would only add credits.

## 4. Duplicates

- The store holds 4 snapshots, one per Slam, all at 11:55 on day 2. The only Wimbledon one is 2022-06-28T11:55Z.
- No planned snapshot equals it. The planner refuses any group whose expected snapshot is already stored, and the
  runner skips any request whose ID is in the ledger or whose expected snapshot is in the store.
- The 11:55 snapshot is still used legitimately, but only for decisions at or after 11:55, through the normal
  point-in-time selector.

## 5. Discovered-later vs available-before-decision

- **4 events** (decisions 11:00 ×2 and 11:10 ×2) were *discovered* in the 11:55 snapshot, after their own decisions.
  - Planning them from that schedule is outcome-blind. It chooses which past snapshot to buy, not what was known.
  - Their admissibility comes **only** from the 10:55 snapshot: the selector admits quotes observed ≤ decision, so
    the 11:55 snapshot can never serve them.
  - If they are absent from the 10:55 snapshot, they stay in the denominator as unavailable. The ledger records
    `events_present` per request, so absence is visible.
  - The audit reports them as a separate subgroup.
- **35 events** have decisions at or after 11:55. Their 11:55 listing shows the schedule was public before their
  decision, but the go criteria still use only their decision-time snapshot plus anything observed earlier.
- **Reschedules:** decisions use the frozen anchor, the earliest scheduled start seen in *any* snapshot.
  - If a decision-time snapshot shows an earlier start, the decision moves earlier, possibly before its snapshot.
    The event is then conservatively unavailable and counted in `anchor_moved_events`.
  - The anchor rule is not changed.

## 6. Execution procedure (owner-run, only after authorisation)

New commands:
- `odds stage-plan`: offline manifest.
- `odds stage-run`: a **dry run by default**. Paid calls need both `--execute` and `--approve-paid-calls`.
- `odds stage-audit`: offline go/no-go.

The per-call `--max-credits` of `capture-historical` is **not** used. The stage ceiling lives in a cumulative
append-only ledger (`ledger.jsonl`, fsynced per entry).

1. `git pull`. Confirm a clean tree and the protocol SHA.
2. `uv run quantedge odds stage-plan --name stage-a-wimbledon-2022 --discovery-capture-id cap1_d547a2721400551bc5ad7d03e371022a`
   - Expect 9 requests, 39 events, `planned_credits` 90, manifest SHA `c019aca1…`.
3. `uv run quantedge odds stage-run --name stage-a-wimbledon-2022`
   - Dry run. Expect 9 × `would_request`, cumulative 10 → 90, no ledger file.
4. With `QE_ODDS_API_KEY` in the environment only:
   `uv run quantedge odds stage-run --name stage-a-wimbledon-2022 --execute --approve-paid-calls`
5. Offline, in order: `odds build-tables --stream historical`, `odds link --stream historical`,
   `odds stage-audit --name stage-a-wimbledon-2022`.
6. Report the go/no-go and stop. Stage B needs separate approval.

**Pre-call checks (every request):**
- not already in the ledger, and its snapshot not already stored;
- cumulative + 10 ≤ planned total (90) ≤ stage ceiling ≤ 130;
- last known `x-requests-remaining` ≥ 10 (ledger, else the newest stored capture, 19,960);
- client with `max_retries = 0`, so a 5xx can never be silently re-sent and re-charged;
- client run budget ≤ stage total;
- markets and regions identical to the manifest (h2h × eu).

**Stop immediately** (the paid body is stored and the ledger written first, so nothing is lost) on:
- non-200 or a transport error: nothing is stored and no retry is made;
- `x-requests-last` ≠ 10, or missing;
- `x-requests-remaining` not exactly 10 lower than the last known value (other usage, renewal or double charge);
- a returned snapshot missing, later than requested, or older than planned by more than the gap bound (grid
  assumption failed).

After a stop, re-running resumes: completed requests are skipped, and the ledger total carries over.

## 7. Go / no-go (outcome-blind, fixed in code before any Stage A data)

`stage_audit` evaluates the criteria from the four-Slam audit §5.
- **Decisions:** frozen anchor; 30-min freshness.
- **Denominator:** all **39 planned events**.
- **Quote age:** the selector's `staleness_minutes` for each book's newest admissible complete h2h quote.

| # | Criterion | Threshold |
|---|---|---|
| c1 | Events with ≥ 1 `available` book | ≥ 80 % |
| c2 | Events with ≥ 2 `available` books | ≥ 70 % |
| c3 | Median quote age at decision | ≤ 20 min |
| c4 | Returned snapshot ≤ requested; no source time after observation | 0 violations |
| c5 | Events linked uniquely to canonical matches (identity only) | ≥ 90 % |
| c6 | No quarantine reason outside the probe's (`price_not_above_one`) | none |
| — | Credits spent ≤ planned 90 | required |

- **Also reported:** discovered-later subgroup availability; `anchor_moved_events`; per-request `events_present`;
  15- and 60-min freshness.
- **Diagnostic only, no decision:**
  - At 15 min, the 14 events with gap ≥ 11 min will mostly be stale by construction. This is a sensitivity
    result.
  - No reference-book choice is made from Stage A.
- **Baseline today:** the audit was run on the existing data. Result: 0/39 events available, median age 165 min,
  **no-go**, as expected with only the 11:55 snapshot. It works offline.

**Validation.** The criteria use only odds tables, the ledger and identity links. No outcome, score or model
prediction is read. Linking for 2022 uses the development period only (inner folds), not 2023–24 or 2025+.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Grid offset is not :x5 for every hour or day (inferred from one capture) | Returned snapshot checked after every call; stop if older than planned beyond the gap bound. A later-than-expected (fresher) snapshot is accepted only if ≤ requested |
| Scheduled starts move between snapshots | Frozen earliest-start anchor (conservative exclusion); `anchor_moved_events` reported; protocol unchanged |
| Discovered-later events missing at 10:55 | Kept in the denominator; reported as a subgroup and via `events_present` |
| Failed 5xx calls are charged, or a retry double-charges | `max_retries = 0` enforced; quota reconciled after each call; stop on mismatch |
| Per-call ceiling misread as the stage ceiling | Cumulative ledger cap = planned total; independent client budget ≤ stage total |
| Other usage on the same API key during the stage | Quota reconciliation stops the run; resume after review |
| Empty or partial responses | Stored and recorded (`events_present`); counted as unavailable; c1/c2 decide |
| Market lag outliers (up to 19 min seen) | Max gap 15 keeps p99 lag within 30 min; residual staleness measured by c1–c3 |
| Credentials | Key only from `QE_ODDS_API_KEY`; never in stored params, ledger or output (tested) |
| Licensed data in Git | Bodies, tables, manifest and ledger only under gitignored `data/` |

## 9. Budget arithmetic correction (four-Slam audit §5)

- **The error:** the audit's Stage D row says "1,300 per edition; 9,000 cumulative". 9 editions × 1,300 =
  **11,700**, not 9,000.
- **Where the totals came from:** the programme total of 10,750 = 130 + 320 + 1,300 + 9,000. It is consistent
  with 9,000 for Stage D, not with 1,300 per edition.
- **Status:** this is a budget question for the owner. The scientific protocol is not affected.

| Option | Stage D | Programme A–D | Note |
|---|---|---|---|
| **1 (recommended)** | 1,300 per edition, **11,700** cumulative | **13,450** | Per-edition ceiling covers the one-call-per-match upper bound (~123 matches ≈ 1,230 + discovery) |
| 2 | 1,000 per edition, 9,000 cumulative | 10,750 (unchanged) | Covers the central estimate (~700–800 per edition) but not the upper bound; editions might stop early |

Both options fit within the 19,960 remaining (19,870 after Stage A). The audit's §5 now carries an erratum pointing
here. No ceiling changes without the owner's choice.

## 10. Code changes (tested, offline)

- **`src/quantedge/odds/stage.py`:**
  - `plan_stage`, the manifest and summary;
  - `run_stage` with the ledger and pre- and post-call checks;
  - `stage_audit` with the fixed go/no-go thresholds;
  - `returned_snapshots` and `last_known_quota`.
- **CLI:** `odds stage-plan`, `stage-run`, `stage-audit`.
- **`tests/integration/test_odds_stage.py`:** 30 synthetic tests (scripted transport, no network):
  - grid and offset;
  - grouping and no-later-snapshot invariants, including a Hypothesis property test over random schedules;
  - ceilings at planning;
  - duplicate refusal;
  - manifest round-trip;
  - dry run makes no calls;
  - ledger, resume and no re-buy;
  - stops on charge mismatch, quota drift (including the first call against the stored quota), older-than-planned
    or post-request snapshot, cumulative cap and low quota;
  - unsafe client configuration refused; a non-200 is not retried or stored;
  - missing and rescheduled events;
  - lagging markets;
  - CLI: plan, dry run, and refusal of `--execute` without approval.

## 11. Readiness verdict

**READY for Stage A, pending owner authorisation:**
- 9 paid historical calls;
- **90 credits** planned;
- cumulative cap 90, absolute ceiling 130 never auto-used;
- expected quota after Stage A: 19,870.

No paid request has been made. Also needed from the owner: a choice on the Stage D budget correction (§9) before
Stage D is ever planned.
