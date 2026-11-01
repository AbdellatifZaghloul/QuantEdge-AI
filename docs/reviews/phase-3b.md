# Phase 3B review — pilot preparation, timing hardening, pre-registration

Date: 2026-11-01. **No API plan purchased, no key used, no network call to the provider, zero credits spent.**
- Phase 2 results are unchanged.
- 2023–24 outcomes were not evaluated, so the remaining late-check look is preserved.
- The 2025+ holdout was not accessed.

## 1. Deliverables

| Item | Where |
|---|---|
| Pilot specification and pre-registration | `docs/preregistration/market-benchmark-v1.md`; machine-readable `configs/preregistration/market_benchmark_v1.yaml` (SHA-256 `e3d3f642…67da21`) |
| Pre-registered analysis code | `quantedge.odds.pilot` (`run_pilot`, `pilot_population`), `quantedge.odds.preregistration` (window guard, outcome-blind reference choice), CLI `quantedge odds pilot-compare` (refuses without acquired data) |
| Offline credit planner | `quantedge.odds.planner`, CLI `quantedge odds plan` (dry run: zero network calls, zero credits) |
| Timing hardening | `quantedge.odds.selector`, `quantedge.odds.tables` (§2) |
| Forward-capture experiment spec | `docs/specs/forward-capture.md` (not active) |
| Decision log | `docs/DECISIONS.md` D-27 to D-30 |

## 2. Timing integrity changes

**Quarantine instead of repair.** The following are now excluded from selection, and a decision left with nothing
else gets status `quarantined_timestamp`:
- a source timestamp (`market_last_update`) later than the observation;
- a source timestamp later than the **decision**;
- a historical snapshot stamped later than the time requested (new flag `snapshot_after_request`);
- quotes observed at or after the start.

**Missing source timestamps** are rejected by default, with status `source_time_missing`. The lenient
`use_observed` option exists only for explicit sensitivity work. Previously, a missing source time silently fell back
to the observation time.

**Reschedules.**
- `odds_events.commence_time_earliest_utc` records the earliest start ever shown.
- Decisions anchor on it: a later reschedule is only known later and must not move the decision.
- Rescheduled events keep the `commence_time_changed` flag.

**Non-timing flags** such as `odds_out_of_range` are reported but do not block. Short prices like 1.01 can be
genuine.

**Information mismatch.** Elo features use the day-bound cutoff (earliest instant of the earliest possible match
day), while market quotes may be observed up to 60 minutes before start. The market sees more, including results
the model may not. This is stated in the protocol, and no comparison is presented as evidence of a trading signal.

## 3. Offline plan — real canonical data (build `cca7ad5d59054438`, development view)

**Probe** (requires future approval):

| # | Request | `date` (UTC) | Market × region | Credits |
|---|---|---|---|---|
| 1 | `/v4/historical/sports/tennis_atp_aus_open_singles/odds` | 2022-01-18T12:00:00Z | h2h × eu | 10 |
| 2 | `/v4/historical/sports/tennis_atp_french_open/odds` | 2022-05-24T12:00:00Z | h2h × eu | 10 |
| 3 | `/v4/historical/sports/tennis_atp_wimbledon/odds` | 2022-06-28T12:00:00Z | h2h × eu | 10 |
| 4 | `/v4/historical/sports/tennis_atp_us_open/odds` | 2022-08-30T12:00:00Z | h2h × eu | 10 |
| | **Total (ceiling 40)** | | | **40** |

Probe details:
- **Timing:** day 2 of each 2022 main draw at 12:00 UTC, when many first-round matches are still upcoming.
- **What it answers:**
  - which bookmakers price these events;
  - whether `pinnacle` appears;
  - whether market-level `last_update` is present and fresh;
  - player naming and start-time behaviour.
- **What it does not do:** outcomes are **not** joined during the probe. Each snapshot returns whatever the provider
  had then, and that content is unknown in advance.

**Full pilot estimate** (a separate approval, not part of the probe):

| Item | Value |
|---|---|
| Editions | 10 Slam editions 2020–2022, all at the documented 10-minute pre-Sept-2022 cadence |
| Expected eligible population | 1,228 main-draw matches |
| Step 1: historical events calls | 1 credit each, 1 per tournament day, 15 per edition (150 total), to learn scheduled starts |
| Step 2 (offline) | Group decision times into 30-minute slots |
| Step 3: historical odds calls | One per non-empty slot, 10 credits each |
| **Credits** | Floor 1,650 (one snapshot per day, which cannot meet the 30-minute freshness rule for most matches). **Upper bound 12,430** (one snapshot per match). The realistic figure becomes known only after step 1 |

**Unknowns:**
- bookmakers per event;
- whether Pinnacle is present;
- the share of complete two-way quotes;
- actual start times;
- how far `last_update` lags the snapshot.

The plan lists *scheduled requests* only. *Returned quotes* are unknown until executed.

## 4. Tests

- **Local (Windows):** **259 passed**, of which 11 use real data.
- **CI-equivalent synthetic suite:** 248 passed.

New tests:

| Area | Tests |
|---|---|
| Timing (leakage) | Source later than observation; source later than decision; each blocking flag; benign flags allowed; missing source rejected by default and allowed only by explicit option; a valid quote still chosen beside invalid ones; property test that selected rows always satisfy `source ≤ observed ≤ decision < start` |
| Normalisation | Snapshot later than requested is flagged and quarantined; reschedules keep the earliest start, the decision anchors on it, and quotes are flagged |
| Pre-registration | Protocol loads, fingerprint stable; window guard refuses late-check, holdout and pre-window rows; reference choice outcome-blind (refuses outcome columns), preferred if ≥ 80 %, else highest availability with alphabetical ties |
| Planner | No network (socket connect patched to fail); probe ≤ 40 credits; > 40 refused; exact cost arithmetic |
| Pilot | Synthetic end to end: fallback rule triggered when Pinnacle covers 75 %; identical-population scoring; log-loss and Brier paired differences; staleness and de-vig sensitivities; quote-age quantiles; window refusal |
| Real data (local) | Probe = 4 × 10 credits, all 2022 editions; population 1,228; 10 editions; 10-minute cadence |

CI on Linux and Windows runs on push (see the commit's Actions run).

## 5. Supplying the API key locally (only after authorisation)

1. Create the key in the provider account. Never paste it into chat, issues, commits, notebooks or configs.
2. Store it as a user environment variable on this machine (PowerShell):

   ```powershell
   [Environment]::SetEnvironmentVariable("QE_ODDS_API_KEY", "<your-key>", "User")
   ```

   Then open a new terminal. For a single session only: `$env:QE_ODDS_API_KEY = "<your-key>"`.
   The code reads only `QE_ODDS_API_KEY` from the process environment. It does not read `.env` files automatically.
   `.env` remains gitignored, and `.env.example` holds only an empty placeholder.
3. Verify with the **free** endpoint: `uv run quantedge odds sports`. It costs 0 credits and prints the remaining
   quota.
4. If the key is ever exposed, revoke it in the provider dashboard and remove the variable:

   ```powershell
   [Environment]::SetEnvironmentVariable("QE_ODDS_API_KEY", $null, "User")
   ```

## 6. Owner approval checklist — required before any purchase or chargeable query

- [ ] **A1 Purchase:** approve a specific plan (e.g. 20K credits, $30/month) and its billing period.
- [ ] **A2 Key handoff:** key stored locally as in §5. Confirm it is not in any file under the repository.
- [ ] **A3 Probe:**
  - approve exactly the four requests in §3, total ≤ 40 credits, run with `--approve-paid-call --max-credits 10`
    per request;
  - no other calls except the free `/sports`.
- [ ] **A4 Probe review:**
  - after the probe, review bookmaker and Pinnacle availability and timestamp quality **without outcomes**;
  - decide whether to proceed.
  - The pre-registered protocol is not changed in response. If it must change, issue `market-benchmark-v2` before
    any outcome is joined.
- [ ] **A5 Full pilot budget:**
  - approve step 1 (≤ 150 credits) and a hard ceiling for step 3, which is known after step 1;
  - the upper bound is 12,430 credits.
- [ ] **A6 Confirmatory run:** approve the single registered `quantedge odds pilot-compare` run.
- [ ] **A7 Forward capture** (separate): approve schedule, monthly credit ceiling and machine, per
  `docs/specs/forward-capture.md`.
- [ ] **A8 Late-check look:** confirm that 2023–24 stay excluded. Using them would consume the last `phase2-elo-v1`
  look.

## 7. Limitations

- Only 10 bootstrap blocks exist in the pilot window (the block is the ISO week of the tournament start, so one per
  edition). Intervals will be wide.
- The population is Grand Slams only, which is unrepresentative of the full ATP calendar.
- Coverage of the events-call schedule and the provider's snapshot contents is unverified until A3.
- The forward-capture cadence and quota are estimates.
