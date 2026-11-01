# Phase 3A review — odds infrastructure, provider assessment, market baselines

Date: 2026-11-01. Scope: odds data foundation only.
- **No API key was used and no paid or chargeable request was made.**
- No odds were downloaded.
- No betting strategy, staking or profitability claim exists.
- Phase 2 results and runs are unchanged.
- The 2025+ holdout outcomes were not read for any modelling or selection.

## 1. What was built

| Component | Module | Notes |
|---|---|---|
| Provider-independent interface | `odds/provider.py` | Fetch (`RawCapture`) is separate from pure normalisation (`NormalizedSnapshot`), so new providers add an adapter, not a pipeline |
| The Odds API adapter | `odds/theoddsapi.py` | See "Adapter safety" below |
| Append-only capture store | `odds/store.py` | See "Capture store" below |
| Canonical odds tables | `odds/schemas.py`, `odds/tables.py` | `odds_events`, `odds_quotes`, `event_links` (Pandera-validated). Deterministic quote and event IDs, quality flags. Every quote carries `price_semantics = observed_quote_not_guaranteed_executable` |
| Identity reconciliation | `odds/identity.py` | Accent/punctuation normalisation; name order; provider alias table; same-name players disambiguated only by activity *before* the event; fuzzy similarity only *suggests* aliases. Linking needs a unique pair, start-time consistency with the match's day-bound window, a curated tournament alias (anchored regex and level), and no competing provider event |
| Point-in-time selector | `odds/selector.py` | See "Selector rules" below |
| De-vig | `odds/devig.py` | Proportional, power and Shin (§4) |
| Market baselines and comparison | `odds/baseline.py` | Raw implied and de-vigged P(A) per bookmaker; median consensus (a probability baseline, not a price); comparison on the identical population with paired block bootstrap; refuses holdout rows |
| Coverage and cost assessment | `odds/coverage.py` | Offline: documented coverage table × canonical data |
| CLI | `quantedge odds coverage / sports / capture-live / capture-historical / build-tables / link / compare` | |
| Repo guard | `devtools/hygiene.py` | JSON/JSONL outside `tests/` now refused, so raw provider responses cannot be committed |

**Adapter safety:**
- The key is read only from `QE_ODDS_API_KEY`. It is never stored, logged or echoed, and it is redacted from errors.
- A per-run credit ceiling (default **0**) is checked *before* any chargeable request.
- Historical calls additionally need explicit approval.
- Retries use exponential backoff with jitter, honour `Retry-After`, and cover 429, 5xx and network errors. 401/422 are not retried.
- The adapter tracks the quota headers.

**Capture store:**
- Content-addressed bodies are written with exclusive create and then made read-only.
- Each observation is one line in an append-only log with an fsync.
- Re-ingesting the same observation is idempotent.
- Every read re-hashes the body, so tampering is detected.
- The prospective (forward) and historical streams are stored separately under `data/raw/commercial/` (gitignored).

**Selector rules:**
- Only timestamped quotes are used.
- A quote is admissible only if observed at or before the decision time, before the scheduled start, with the decision itself before the start.
- Both selections must come from the same bookmaker *and the same observation*.
- A quote is stale if its market `last_update` is older than the configured limit.
- Every gap gets an explicit status: `no_quote_before_cutoff`, `untimed_not_admissible`, `decision_after_commence`, `incomplete_market` or `stale`.

## 2. The Odds API — verified 2026-11-01 (official pages)

| Topic | Finding |
|---|---|
| Endpoints and cost | `/v4/sports`: no cost. `/v4/sports/{s}/events`: no cost. `/v4/sports/{s}/odds`: `markets × regions`. Event odds: `unique_markets × regions`. `/v4/historical/sports/{s}/odds?date=`: `10 × markets × regions`. Historical events: 1 (0 if empty) |
| Historical wrapper | `timestamp` (closest snapshot ≤ requested `date`), `previous_timestamp`, `next_timestamp`, `data` |
| Snapshot cadence | Featured markets from 2020-06-06 at 10-minute intervals, 5-minute from Sept 2022. Additional markets from 2023-05-03 at 5-minute intervals |
| Live refresh | Pre-match 60 s, speeding up from 6 h before start towards the in-play 40 s |
| Freshness field | "The market-level `last_update` field shows the last time our system saw odds for that market from the bookmaker"; suspended markets stop updating and are removed after about 15 minutes. **The bookmaker-level `last_update` is deprecated** (ignored by our adapter) |
| Quota headers | `x-requests-remaining`, `x-requests-used`, `x-requests-last` |
| Rate limiting | HTTP 429 exists. **Frequency limits are not documented [UNVERIFIED]**, so the adapter uses backoff and `Retry-After` |
| Tennis coverage | 25 ATP sport keys, per tournament and only "in season". Historical start dates are in `reference/odds_api_tennis_coverage.csv`, unchanged from Phase 0.5. **Not covered:** ATP 250, Challenger, ATP Finals and some 500s |
| Markets | "Match winner odds are mainly covered … Limited coverage is also available for game spreads and game totals (over/under) from selected bookmakers." Historical depth for tennis totals and spreads is **[UNVERIFIED]** without a paid probe |
| Accuracy | "Data errors aren't common but can occur … they can still be present in historical snapshots." |
| Executability | The documentation makes **no** claim that listed odds were executable. Our schema labels every quote as observed only |
| Plans and terms | Historical data requires a paid plan (cheapest listed: 20K credits, $30/month). The terms permit storing data and training models, including commercially, if the data is not the product resold; no redistribution (Phase 0.5 check). Licence class: `COMMERCIAL_LICENSED` |

**Offline coverage assessment** (`quantedge odds coverage`, canonical build `3d85fd99efdebfc7`, main draws only):

| Window | Main-draw matches | Of which eval-eligible |
|---|---|---|
| Development (≤ 2024-12-31) | **2,577** | **2,494** |
| Holdout (integrity count only) | 2,220 | — |

Development coverage by tournament:

| Tournament(s) | Eligible matches |
|---|---|
| US Open 2020–24 | 605 |
| Roland Garros 2020–24 | 619 |
| Australian Open 2021–24 | 495 |
| Wimbledon 2021–24 | 492 |
| Five Masters, late 2024 only | 31–94 each |

Cost:
- A plan of one historical snapshot per tournament day costs 10 credits per day for one market and one region.
- Over all 25 keys that is about 6,090 credits, including holdout years.
- These counts come from our own data. Whether each match actually has quotes, and from which books, is unknown until a probe.

## 3. Point-in-time decision time — a design finding
- The canonical prediction cutoff (the earliest instant of the earliest possible match day) is often *before every
  available pre-match snapshot*. Applied to odds, the strict selector correctly returns `no_quote_before_cutoff`.
- Market decisions therefore use **the provider's scheduled start minus a lead** (default 60 min,
  `decisions_from_schedule`), as ARCHITECTURE.md §5.3 rule 2 already specifies.
- Model features keep their earlier day-bound cutoff. In any model-vs-market comparison the market is therefore
  given at least as much information as the model, which is conservative against the model.
- A provider start time is a *schedule*, not a guaranteed start. Delayed matches keep the earlier decision time.

## 4. De-vig methods

With odds `o_i`, implied `q_i = 1/o_i` and booksum `Q = Σ q_i`:
- **Proportional:** `p_i = q_i / Q`.
- **Power:** `p_i = q_i^k` with `Σ p_i = 1` (`k > 1` when `Q > 1`).
- **Shin:** `p_i = (√(z² + 4(1−z) q_i²/Q) − z) / (2(1−z))`, with `z` solved numerically.

Justification for the two alternatives:
- Both power and Shin move margin towards longshots, matching the documented favourite–longshot bias.
- Shin's method is derived from an explicit insider-trading pricing model (Shin 1993; Jullien & Salanié 1994).
- Which method fits tennis outcomes best is decided empirically on development data once odds exist. No method is
  preferred a priori.

Tests:
- Shin agrees with the independent `shin` package (MIT, test-only) to 1e-9.
- Property tests (hypothesis) check that results sum to 1, stay in (0,1), preserve ordering, and leave margin-free
  books unchanged.
- Power and Shin give the favourite at least the proportional probability.
- Underrounds (booksum < 1) are refused by power and Shin and flagged by proportional.
- A floating-point edge case (booksum 1 − 1e-16 refused) was found by property testing and fixed.

## 5. Market vs Elo comparison — status
- **Not run on real data: no licensed, timestamped historical odds exist in this project.**
- The harness is implemented (`quantedge odds compare --run-id …`) and tested end to end on synthetic data.
- It joins market consensus probabilities with the Phase 2 *inner-fold* test predictions (2013–2022 only), scores
  only rows where every column is present, and refuses holdout rows.
- Odds-covered inner-fold years are only 2020–2022 (Slams). 2023–24 are late-check years (§7, Q7).

## 6. Tests

**Local (Windows): 244 passed.** That is 234 synthetic plus 10 real-data tests.

| Area | Tests |
|---|---|
| De-vig | Known values, oracle agreement, 3 property suites, invalid inputs |
| Adapter | Costs; missing key; default zero budget sends nothing; historical approval gate; budget accumulation; retry with `Retry-After`, then success; non-retryable errors; retry exhaustion; key redaction; live and historical normalisation |
| Store | Append-only; idempotent; content stored once; read-only bodies; tamper detection; deterministic tables with provenance |
| Identity | Accents, punctuation, name order; same-name disambiguation using only pre-event activity; fuzzy match never accepted; linked, time-inconsistent, unmatched and unresolved events; tournament alias mismatch; ambiguous candidates; duplicate provider events; side mapping |
| Selector (leakage) | Latest admissible quote; every explicit status; property test that no selected quote is after the decision or in-play, and that rewriting every quote after the decision changes nothing |
| Pipeline | Capture → store → tables → link → select → de-vig → consensus → identical-population comparison; day-bound cutoff yields no quotes; holdout refused; orientation mapping |
| CLI | Capture refused without confirmation; zero budget blocks; historical refused without approval; key never printed; compare fails cleanly without data |
| Hygiene | JSON/JSONL outside `tests/` flagged |

CI results are recorded in the commit message and the delivery summary.

## 7. Unresolved questions before any historical odds acquisition

1. **Spending approval.** Approve a paid Odds API plan (the cheapest historical-capable plan is 20K credits, $30/month)
   and set a per-run ceiling (`--max-credits`). Today every chargeable call is blocked.
2. **Credential handoff.** Put the key in a local, gitignored `.env` or a user environment variable `QE_ODDS_API_KEY`.
   It must never be pasted into chat, issues or commits.
3. **Probe design** (about 20–40 credits). For example, one historical snapshot each for Wimbledon 2023 and Paris 2024,
   to verify the following before any bulk pull:
   - which bookmakers price ATP matches;
   - whether `pinnacle` (EU region) is present;
   - whether tennis totals and spreads exist historically.
4. **Benchmark book.** Choose the benchmark: a single named sharp book if present, or the median consensus. This is
   pre-registered before any comparison.
5. **Snapshot schedule.** Choose between fixed daily snapshot times (cheap; possibly stale for late matches) and
   per-match times derived from the free/1-credit events endpoints (more calls). This sets the credit budget.
6. **Decision timing.** Defaults are a lead of 60 min before the scheduled start and a staleness limit of 30 min.
   Confirm them, or pre-register other values, before any evaluation.
7. **Late-check years.** Odds-covered development years are 2020–2024. 2023–24 are Phase 2 late-check years with one
   look remaining for `phase2-elo-v1`. Decide whether market-vs-Elo on 2023–24 counts as that look, or restrict
   comparisons to 2020–2022 (1,228 eligible Slam main-draw matches).
8. **Statistical power.** At most about 2,500 eligible development matches, mostly Slams. Accept that CIs will be
   wide, or add another timestamped source.
9. **Tier C benchmark.** Decide whether to manually download tennis-data.co.uk for a private, untimed benchmark over
   2005–2024. This needs an Excel reader dependency and must never be published.
10. **Forward capture operations.** Choose where and how often prospective capture runs (a local scheduler only;
    secrets and data must not go to CI), and the monthly credit ceiling.
11. **Alias curation.** Decide who reviews unmatched-name suggestions before entries are added to
    `reference/player_name_aliases.csv`.
12. **Day-bound validation (D-18).** Agree to use linked start times to validate and, where violated, widen
    `round_day_bounds.csv`. A widening changes the build ID and requires re-running Phase 2.

## 8. Reproduce

```bash
uv sync --locked
uv run quantedge odds coverage                    # offline coverage + credit estimate
uv run pytest                                     # all tests (real-data tests need data/)
# Only after approval and with QE_ODDS_API_KEY set locally:
uv run quantedge odds sports                      # free
uv run quantedge odds capture-historical --sport tennis_atp_wimbledon --at 2023-07-05T09:00:00Z \
    --approve-paid-call --max-credits 10
uv run quantedge odds build-tables --stream historical
uv run quantedge odds link --stream historical
uv run quantedge odds compare --run-id 0a5b056019ec --stream historical
```
