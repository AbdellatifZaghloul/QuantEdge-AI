# Forward live-odds capture — experiment specification (NOT ACTIVE)

Status: specification only. Nothing is scheduled or running. Activation requires the approvals in
`docs/reviews/phase-3b.md` §6.
- This is separate from historical backtesting.
- Captured data form the **prospective** stream, which is never joined to 2025+ holdout outcomes and never
  re-selected retrospectively.

## Scope

- One region (`eu`) and one market (`h2h`, match winner).
- The ATP sport keys that are in season, discovered with `/v4/sports?all=true` (free).
- **Bookmaker odds only.**
  - Live point-by-point tennis statistics (point outcomes, serve and return data) are not part of the odds feed.
  - The Odds API's separate `/scores` endpoint is unverified for tennis and would at most give scores, not
    point-level statistics.
  - Point-level data needs its own licensed provider and adapter, and is out of scope.

## Schedule (local only)

- **Runner:** the owner's machine via Windows Task Scheduler (or cron). No CI runner holds the key or the data.
- **Cadence (proposal):**
  - every 30 minutes from 00:00 to 23:30 UTC while at least one ATP key is in season;
  - one extra capture 70 minutes before each known scheduled start.
- **Command per capture:**

  ```
  uv run quantedge odds capture-live --sport <key> --confirm-credit-use --max-credits <n>
  ```

- **Quota:**
  - Live odds cost `markets × regions` = 1 credit per call.
  - 48 captures/day × number of in-season keys (typically 1–2) ≈ 50–100 credits/day, or about 1,500–3,000 per month
    in active weeks.
  - A monthly ceiling must be approved before activation. The command's `--max-credits` caps each run.

## Timestamp semantics

| Field | Meaning |
|---|---|
| `captured_at_utc` | Our request time (UTC, tz-aware) |
| `observed_at_utc` | The same as `captured_at_utc` for live captures |
| `market_last_update_utc` | Provider's last sighting of the market (source time) |
| `commence_time_utc` | The schedule as shown in that capture. The earliest version is kept for decisions |

## Provenance and retention

- **Storage:**
  - append-only, content-addressed bodies (exclusive create, then read-only);
  - an fsync'd observation log;
  - SHA-256 verification on every read;
  - no retroactive edits;
  - re-ingesting the same observation is a no-op.
- **Location:** `data/raw/commercial/the_odds_api/prospective/` (gitignored). Never committed, redistributed or
  published at row level. Only aggregate results may be shared.
- **Retention:** kept indefinitely for research, which the provider's terms permit. Deletion only on owner request,
  logged with the file hashes removed.

## Failure handling

| Failure | Behaviour |
|---|---|
| 429 / 5xx / network | Retry with exponential backoff and jitter (honours `Retry-After`), at most 4 retries, then the run fails and logs |
| 401 / 422 | No retry; the run fails and alerts the owner (bad key or parameters) |
| Budget | A run refuses to start a request that would exceed `--max-credits` |
| Partial day | Missed captures are not back-filled from historical data inside the prospective stream; gaps stay visible |
| Clock | Captures use UTC from the system clock. Large clock drift is a known risk and should be checked with NTP |

## Evaluation rules (when eventually used)

- The same pre-registered selector rules apply (`market-benchmark-v1` timing).
- Prospective results are reported separately from any historical backtest.
