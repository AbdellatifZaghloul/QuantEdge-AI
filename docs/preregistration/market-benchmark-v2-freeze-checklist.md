# `market-benchmark-v2` — owner freeze checklist (V2 is NOT FROZEN)

Date: 2026-11-02.

**Status:** candidate only.
- Candidate text: `docs/preregistration/market-benchmark-v2-CANDIDATE.yaml`. A test pins it to the code constants and
  to the calendar, and asserts that it is not frozen.
- Draft prose: `docs/preregistration/market-benchmark-v2-DRAFT.md`.
- Evidence: `docs/reviews/market-benchmark-v2-design-review.md` §13.

**How this package was prepared:**
- No odds-provider calls and no credits.
- No outcomes.
- Public web pages were read only to verify calendars, start-of-play times and provider pricing documentation.

## 1. Calendar evidence (10 editions)

**What counts as verified.** Every calendar row stays `verified = false`, because no row has every field confirmed
by an official tournament source. The grades used below:
- **primary:** an official tournament site or official order of play;
- **tour-official:** WTA or ATP site;
- **secondary:** encyclopedia or press;
- **assumption:** not sourced.

| Edition | Main-draw dates (secondary, source) | Rest days | Expected days without men's singles |
|---|---|---|---|
| AO 2021 | 8–21 Feb 2021 ([Wikipedia](https://en.wikipedia.org/wiki/2021_Australian_Open)) | none (assumption) | Sat 20 Feb (assumption) |
| AO 2022 | 17–30 Jan 2022 ([Wikipedia](https://en.wikipedia.org/wiki/2022_Australian_Open)) | none (assumption) | Sat 29 Jan (assumption) |
| RG 2020 | 27 Sep – 11 Oct 2020, moved by COVID-19 ([Wikipedia](https://en.wikipedia.org/wiki/2020_French_Open)) | none (assumption) | Sat 10 Oct (assumption) |
| RG 2021 | 30 May – 13 Jun 2021 ([Wikipedia](https://en.wikipedia.org/wiki/2021_French_Open)) | none (assumption) | Sat 12 Jun (assumption) |
| RG 2022 | 22 May – 5 Jun 2022 ([Wikipedia](https://en.wikipedia.org/wiki/2022_French_Open)) | none (assumption) | Sat 4 Jun (assumption) |
| Wimbledon 2021 | 28 Jun – 11 Jul 2021 ([Wikipedia](https://en.wikipedia.org/wiki/2021_Wimbledon_Championships)) | **Sun 4 Jul (secondary):** "final edition of Wimbledon to have no matches scheduled on Middle Sunday" | Sat 10 Jul (assumption) |
| Wimbledon 2022 | 27 Jun – 10 Jul 2022 ([Wikipedia](https://en.wikipedia.org/wiki/2022_Wimbledon_Championships)) | **none (secondary):** "first edition with a scheduled order of play on ... Middle Sunday" | Sat 9 Jul (assumption) |
| US Open 2020 | 31 Aug – 13 Sep 2020, no spectators ([Wikipedia](https://en.wikipedia.org/wiki/2020_US_Open_(tennis))) | none (assumption) | Sat 12 Sep (assumption) |
| US Open 2021 | 30 Aug – 12 Sep 2021 ([Wikipedia](https://en.wikipedia.org/wiki/2021_US_Open_(tennis))) | none (assumption) | Sat 11 Sep (assumption) |
| US Open 2022 | 29 Aug – 11 Sep 2022 ([Wikipedia](https://en.wikipedia.org/wiki/2022_US_Open_(tennis))) | none (assumption) | Sat 10 Sep (assumption) |

**Counts:**

| Item | Editions |
|---|---|
| Main-draw date ranges matching a dated secondary source | **10 / 10** (also consistent with canonical start dates) |
| Rest-day status sourced | **2 / 10** (Wimbledon 2021, 2022) |
| Expected days without men's singles sourced | **0 / 10** |
| Fully verified from official sources | **0 / 10** |

**Other notes:**
- AO 2021 had a five-day lockdown (13–17 Feb) during which play continued behind closed doors (Wikipedia).
- Rest and empty days are still requested, so an error in either column changes only the reporting, never the
  acquisition calendar.

## 2. Start-of-play evidence (is 10:00 local before normal main-draw play?)

| Edition | Evidence | Grade | Source |
|---|---|---|---|
| RG 2020 | "Play will commence at 11:00am on all courts" (day 1, 27 Sep) | primary | [rolandgarros.com](https://www.rolandgarros.com/en-us/article/order-of-play-sunday-day-1-rg2020-murray-wawrinka) |
| RG 2022 | "play from 11:00am on all courts" (day 1, 22 May) | primary | [rolandgarros.com](https://www.rolandgarros.com/en-us/article/order-of-play-sunday-day-1-rg2022) |
| Wimbledon 2021 | Official order of play dated 2 Jul 2021: outside courts 11:00, No. 1 13:00, Centre 13:30 | primary | [wimbledon.com PDF](https://www.wimbledon.com/en_GB/scores/2021/schedule/pdf/schedulePDF12.pdf) |
| US Open 2020 | "Play begins on all courts at 11am unless otherwise stated"; Arthur Ashe 12 noon (day 1) | tour-official | [wtatennis.com](https://www.wtatennis.com/news/1794596/us-open-2020-day-1-order-of-play) |
| AO 2021 | Rod Laver Arena day session "12 midnight UK time" = 11:00 AEDT (day 2) | secondary | [TNT Sports](https://www.tntsports.co.uk/tennis/australian-open/2021/australian-open-2021-order-of-play-naomi-osaka-serena-williams-novak-djokovic-in-day-one-action_sto8103183/story.shtml) |
| AO 2022 | Play from 11:00 AEDT on day 1 (search summary only); 2022 provider listings: earliest start 11:00 local | weak secondary | — |
| US Open 2021 | Outer courts 11:00, Arthur Ashe 12:00 (day 1), from a search summary; readable primary page not obtained | weak secondary | — |
| RG 2021 | Not found | assumption | — |
| Wimbledon 2022 | Official PDF returned HTTP 403; 2022 provider listings: earliest start 11:00 local | provider evidence only | — |
| US Open 2022 | Not found; 2022 provider listings: earliest start 11:00 local | provider evidence only | — |

- **Conclusion:** the evidence supports an **11:00 local standard start of play** at all four Slams. It is
  established on **sample days only**: primary or tour-official for 4 editions, secondary for 1, weak for 2, and only
  2022 provider listings for the other 3. **It is not established for every day.**
- **Known later starts** (finals, show-court-only days, rain) only lengthen the horizon. They are not exceptions to
  validity.
- **Earlier starts:** none were found. If one occurred, the frozen rules handle it without any change:
  - a listing < 60 min after 10:00 local is `short_lead` and excluded;
  - an actual start before the decision is censored.
- **Verdict:** 10:00 local is a defensible decision point. The residual risk is coverage, not validity.

## 3. Provider charging and the programme budget

- **Price:** "The usage quota cost for historical odds is 10 per region per market" ([The Odds API v4
  docs](https://the-odds-api.com/liveapi/guides/v4/)). For h2h × eu that is **10 per request**.
- **Empty responses:** "Responses with empty data do not count towards the usage quota" (same page).
  - A rest-day or no-men's-singles snapshot is usually not empty: it still lists upcoming events, so it is
    **charged 10**.
  - A truly empty snapshot is charged 0, and the runner then **stops** on the charge mismatch (expected 10). That is
    conservative, and it is the right behaviour for the feasibility run: an empty Slam day is an anomaly.
- **Budget, assuming every request is charged:**

| | Credits |
|---|---|
| Base: **143 requests × 10** | **1,430** |
| Separate contingency (never used automatically; needs its own approval) | 143 |
| Maximum if the contingency is approved | **1,573** |
| Feasibility (US Open 2021 week 1): tranche 1 + tranche 2 | **20 + 50 = 70** (part of the 1,430; not additional) |
| Remaining credits | 19,780 |

## 4. Two-tranche feasibility execution (implemented and tested)

- **Plan:** a write-once `tranches.json` (SHA-256 `605a7eea…1411`) sits next to the untouched manifest
  (`df880fd4…aceb2`), with 7 fixed requests at 14:00 UTC.
  - **Tranche 1:** requests 01–02 (08-30, 08-31), at most **20** credits.
  - **Tranche 2:** requests 03–07, at most **50** more (cumulative cap 70).
- **Separation:** a whole-manifest `stage-run` is refused. Each tranche needs its own
  `--execute --approve-paid-calls` invocation, with a client budget no larger than that tranche's cap.
- **Gate:** tranche 2 requires `tranche-1-gate.json` with status `pass`, written by the offline `v2-tranche-check`.
  It passes only if:
  - both tranche-1 requests are paid, and nothing beyond them;
  - both snapshots lie in `(T_d − 10 min, T_d]`;
  - cumulative spend ≤ 20;
  - the tranche never stopped;
  - each of the two days is observed with ≥ 1 unique canonical main-draw match.
- **When the gate is not `pass`:** anything missing makes it `inconclusive` and any violation makes it `fail`.
  Neither unlocks tranche 2. The gate is write-once, so it cannot be recomputed into a pass.
- **Tampering:** the gate stores a hash of the ledger prefix. Any edit of the ledger after the gate blocks tranche 2.
- **Stops:** any runner stop writes `tranche-N-stopped.json`, which blocks every tranche until owner review.
- **Unchanged rules:** request times are never regenerated. The strict window, quota reconciliation, idempotent
  restart and cumulative cap all still apply.
- **Partial audits:** `v2-audit` checks only the requested days, lists `pending_days` and needs no complete edition
  or lock.

## 5. Checklist before freezing (owner)

- [ ] 1. Confirm or correct each edition's dates against official sources. Record the source and set `verified =
  true` only where every field is confirmed. As of now 0/10 are fully verified.
- [ ] 2. Decide whether the rest-day and expected-empty columns need official confirmation, or stay reporting-only
  assumptions. They do not change acquisition.
- [ ] 3. Accept the start-of-play evidence (§2) as sufficient, with residual coverage risk, or obtain official
  orders of play for RG 2021, Wimbledon 2022 and US Open 2021–22.
- [ ] 4. Set the programme ceiling: recommended **1,430**, with the 143 contingency only by separate approval.
- [ ] 5. Approve the freeze:
  - copy the candidate YAML to `configs/preregistration/market_benchmark_v2.yaml`;
  - set `status: FROZEN` and `frozen_on`;
  - record `calendar_sha256`;
  - record its SHA-256 in DECISIONS.
- [ ] 6. Separately approve **tranche 1 (20 credits)**. Only after its gate passes, separately approve **tranche 2
  (50 credits)**.

## 6. Commands — to be run ONLY after the freeze and the corresponding separate approval

```
# tranche 1 (owner approval 1: 20 credits)
uv run quantedge odds v2-tranche-run --name v2a2-feasibility-uso-2021-wk1 --tranche 1               # dry run
uv run quantedge odds v2-tranche-run --name v2a2-feasibility-uso-2021-wk1 --tranche 1 --execute --approve-paid-calls
uv run quantedge odds build-tables --stream historical
uv run quantedge odds link --stream historical
uv run quantedge odds v2-tranche-check --edition uso-2021 --name v2a2-feasibility-uso-2021-wk1 --tranche 1
# STOP: report the gate. Tranche 2 only with owner approval 2 (50 credits) and a passing gate:
uv run quantedge odds v2-tranche-run --name v2a2-feasibility-uso-2021-wk1 --tranche 2 --execute --approve-paid-calls
uv run quantedge odds build-tables --stream historical
uv run quantedge odds link --stream historical
uv run quantedge odds v2-audit --edition uso-2021 --stages v2a2-feasibility-uso-2021-wk1
```
