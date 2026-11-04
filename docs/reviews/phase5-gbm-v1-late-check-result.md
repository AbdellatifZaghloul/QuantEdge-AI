# `phase5-gbm-v1` late check 2023–2024: result (CONFIRMED)

Date: 2026-11-04. One counted look, authorized by the owner, executed at commit `579838c` with the frozen runner
(`experiment phase5-late-check`), offline (0 network attempts).

**Provenance (verified again without rerunning):**
- Rule `configs/preregistration/phase5_gbm_v1_late_check.yaml` SHA-256 `9a76d4e8ec51b167a2d273e9f946f2131885ca343e49a1ef39ed4f0aef1c5da3`.
- Freeze record `configs/frozen/phase5_gbm_v1.json` SHA-256 `5887fae1431e6d2b63134e05dfddd38d2b301bc21bed0cee8bba0e6ee8e2e1c5`.
- Result `runs/late_check_phase5-gbm-v1_look1/result.json` SHA-256
  `2db430b151b9c589941725ca615c790849cb9bd1a3daa7a3d229a52de3905e65`, equal to the hash in the audit "completed" event.
- Audit trail: one `opened` (2026-11-04T04:01:43Z) and one `completed` (04:03:09Z) event; backup byte-identical.
- Looks: `phase5-gbm-v1` **1**, `phase2-elo-v1` 2. Outputs are gitignored and not committed.

## Primary (pre-registered)

| | Value |
|---|---|
| Matches | 27,134 (2023: 13,175; 2024: 13,959), 100 ISO-week blocks |
| LightGBM v1 log-loss | 0.61092 |
| Frozen Elo log-loss | 0.62666 |
| Paired difference (LightGBM − Elo) | **−0.01574 [−0.01825, −0.01323]** (2,000 replicates, seed 20261103) |
| Decision (`ci_high < 0`) | **CONFIRMED** |

## Secondary (reported only)

| | Result |
|---|---|
| Brier | 0.21207 vs 0.21872; paired −0.00665 [−0.00779, −0.00559] |
| Calibration (pooled) | LightGBM slope 0.998, intercept −0.005, ECE 0.0095; Elo slope 0.998, intercept −0.004, ECE 0.0074 |
| 2023 | log-loss diff −0.01414 [−0.01762, −0.01077]; Brier −0.00615 [−0.00770, −0.00464] |
| 2024 | log-loss diff −0.01724 [−0.02088, −0.01344]; Brier −0.00713 [−0.00870, −0.00544] |
| Grand Slam main (983, 8 blocks) | log-loss diff −0.02835 [−0.04157, −0.01460]; Brier −0.01091 [−0.01640, −0.00512]; both under-confident (slope 1.22 vs 1.42) |
| Without `rounds_into_draw` | −0.01576 [−0.01836, −0.01319] |
| Exclusions / alignment | None; exact alignment with Elo run `2f51db502421`. 64 matches had an unseen tournament-level label (encoded missing, as pre-registered) |

Per-year model log-losses (2023 0.6134 vs 0.6275; 2024 0.6086 vs 0.6258) were computed afterwards from the saved
predictions with the standard metrics code; they are descriptive.

## Limitations

1. Blind for LightGBM only: Elo's 2023–24 results were examined before (2 Phase 2 looks).
2. The result is about probability quality against Elo. It says **nothing** about market efficiency, betting value or
   profitability.
3. Population is Gate-0 eligible matches (walkovers and retirements excluded).
4. The Slam subgroup rests on 8 tournament-week blocks; Slam calibration remains under-confident.
5. `rounds_into_draw` is not strictly point-in-time (sensitivity shows no effect).
6. The 2025+ holdout remains sealed and unread; this is not a holdout result.
