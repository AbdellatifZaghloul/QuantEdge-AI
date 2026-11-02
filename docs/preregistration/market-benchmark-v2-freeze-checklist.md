# `market-benchmark-v2` — owner freeze package (V2 is NOT FROZEN)

Date: 2026-11-03.

**Status:** candidate only.
- Candidate: `docs/preregistration/market-benchmark-v2-CANDIDATE.yaml`, pinned to the code and calendar by a test
  that also asserts it is not frozen.
- Draft prose: `docs/preregistration/market-benchmark-v2-DRAFT.md`.
- Design evidence: `docs/reviews/market-benchmark-v2-design-review.md` §12–§14.

**How the package was prepared:**
- No odds-provider calls, no credits and no outcomes.
- Public organizer pages and PDFs were read only to verify calendars and start-of-play times.
- **Incidental exposure:** one web-search snippet displayed a single Wimbledon 2022 match result (a design-exposed
  edition in the inner folds). It was not sought, recorded or used. Completed-day order-of-play PDFs, which print
  results, were not opened.

## 1. Calendar: first and last main-draw days (organizer evidence, re-checked)

Every source below was retrieved and read for this package. The table records exactly what each source states.
Where a reviewer-supplied link could not be retrieved, another organizer page was used, and that is noted.

| Edition | Days | Grade | What the organizer source establishes | Source |
|---|---|---|---|---|
| AO 2021 | 8–21 Feb 2021 | organizer | "Australian Open 2021 will take place at Melbourne Park from 8 to 21 February" (17 Dec 2020) | [ausopen.com](https://ausopen.com/articles/news/australian-open-set-historic-8-february-start) |
| AO 2022 | 17–30 Jan 2022 | organizer | "will take place at Melbourne Park from 17-30 January 2022" (20 Nov 2021); opening round "commences on Monday, 17 January" ([13 Jan 2022](https://ausopen.com/articles/news/draws-revealed-ao-2022)). The reviewer link `ausopen.com/tickets-ao22` returned HTTP 403 | [ausopen.com](https://ausopen.com/articles/news/united-play-ao-2022-bring-back-big-crowds-and-iconic-summer-vibe-melbourne) |
| RG 2020 | 27 Sep – 11 Oct 2020 | organizer | "from Sunday 27 September to Sunday 11 October" (2 Sep 2020) | [rolandgarros.com](https://www.rolandgarros.com/en-us/article/roland-garros-2020-list-of-participants-singles-women-men) |
| RG 2021 | 30 May – 13 Jun 2021 | organizer | "the main draw will kick off on Sunday 30th May"; event "From 24th May to 13th June", which includes qualifying (12 May 2021) | [rolandgarros.com](https://www.rolandgarros.com/en-us/article/roland-garros-2021-new-tournament-schedule-day-night-sessions) |
| RG 2022 | 22 May – 5 Jun 2022 | organizer | Event "runs from 16 May to 5 June 2022", which includes qualifying and establishes the last day (8 Mar 2022). Day-1 order of play for Sunday 22 May ([21 May 2022](https://www.rolandgarros.com/en-us/article/order-of-play-sunday-day-1-rg2022)) establishes the first main-draw day | [rolandgarros.com](https://www.rolandgarros.com/en-us/article/roland-garros-2022-opening-official-ticketing) |
| Wimbledon 2021 | 28 Jun – 11 Jul 2021 | organizer | Official Compendium 2021, scheduled dates of meeting: "2021 (13) Mon 28 June – Sun 11 July" | [Compendium 2021 PDF](https://www.wimbledon.com/pdf/Wimbledon_Compendium_2021v3.pdf) |
| Wimbledon 2022 | 27 Jun – 10 Jul 2022 | **organizer-derived** | The 2022 visitor guide fixes "Middle Sunday (Sunday 3 July)" as a permanent part of the schedule, and the Compendium says "a 14-day tournament from 2022 onwards". That implies Mon 27 June – Sun 10 July. **Neither date is stated verbatim in the retrievable text.** Secondary: [Wikipedia](https://en.wikipedia.org/wiki/2022_Wimbledon_Championships) states 27 June – 10 July | [2022 guide PDF](https://www.wimbledon.com/pdf/CH_2022_Easy_Access_Guide_v8_DIGI.pdf) |
| US Open 2020 | 31 Aug – 13 Sep 2020 | organizer | "set to be played in Flushing Meadows, N.Y., on its originally scheduled dates of Aug. 31-Sept. 13", without fans (USTA, 17 Jun 2020). The reviewer's usopen.org article is JavaScript-rendered and returned no text | [usta.com](https://www.usta.com/en/home/pro/pro-media---news/2020-us-open--western---southern-open-to-be-held-in-new-york.html) |
| US Open 2021 | 30 Aug – 12 Sep 2021 | organizer | "2021 US OPEN AUGUST 30 - SEPTEMBER 12". The reviewer's usopen.org article is JavaScript-rendered and returned no text | [usopen.org PDF](https://www.usopen.org/pdf/2021-Net-Generation-Virtual-Wildcard.pdf) |
| US Open 2022 | 29 Aug – 11 Sep 2022 | organizer | "The 2022 US Open main draw runs Monday, Aug. 29, to Sunday, Sept. 11" (USTA, 20 Jul 2022) | [usta.com](https://www.usta.com/en/home/pro/pro-media---news/serena-in--u-s--leads-all-nations-in-initial-2022-us-open-single.html) |

- **Result:** first and last days are organizer-stated for **9 / 10** editions and organizer-derived for **1 / 10**
  (Wimbledon 2022).
- **The calendar's dates were confirmed, not changed:** all 143 planned days are identical, and a test asserts this.

## 2. Rest days, expected empty days, and the documentary waiver

| Field | Evidence | Status |
|---|---|---|
| Wimbledon 2021 Middle Sunday (4 Jul) not scheduled | Compendium 2021: "In April 2021 it was announced that, beginning the following year, Middle Sunday would become a permanent part of the schedule". So 2021 was scheduled with Middle Sunday off (organizer, implied); secondary confirms | supported |
| Wimbledon 2022 Middle Sunday (3 Jul) a play day | 2022 visitor guide (organizer) | supported |
| No rest day at AO, RG and US Open | Not sourced | **assumption** |
| Women's-final Saturday without men's singles (all 10) | Not sourced for these years (the Compendium's finals table shows the pattern for earlier Wimbledon years only) | **assumption** |

**Proposed documentary waiver (owner to accept or reject).**
- **Scope:** `rest_days` and `expected_empty_days` are reporting-only labels.
- **Why they cannot affect timing:**
  - every day from the first to the last main-draw day is requested, at the same fixed 10:00 local instant, whatever
    its label;
  - the labels never enter decision times, population rules, budgets or stop rules;
  - a wrong label only mislabels a row (`rest_day` / `expected_no_atp_singles` vs `play_day`) in reports.
- **What the waiver means:** these fields stay explicitly marked as assumptions, and complete-calendar verification
  stays `false`. Freezing requires organizer verification of the **first and last days only**.

## 3. Start of play (the 10:00 local anchor)

| Edition | Evidence | Grade |
|---|---|---|
| RG 2020 | "Play will commence at 11:00am on all courts" (day 1) | primary |
| RG 2021 | "Play begins at 11:00am on all courts" (day 1, [rolandgarros.com](https://www.rolandgarros.com/en-us/article/order-of-play-sunday-day-1-roland-garros-2021)); schedule article: "Matches start at 11am" | primary |
| RG 2022 | "play from 11:00am on all courts" (day 1) | primary |
| Wimbledon 2021 | Compendium 2021: Centre Court 1.30 p.m. (2 p.m. finals days), No. 1 Court 1 p.m., outside courts "11.00 a.m. on at least the first eight days"; the order of play of 2 Jul 2021 is consistent | primary |
| Wimbledon 2022 | Visitor guide: "Matches start on outside courts from 11am, on No.1 Court at 1pm and on Centre Court at 1.30pm, apart from finals weekend when play starts on Centre Court at 2pm" | primary |
| US Open 2020 | "Play begins on all courts at 11am unless otherwise stated"; Arthur Ashe 12 noon (day 1, wtatennis.com) | tour-official |
| AO 2021 | Rod Laver Arena day session at 11:00 AEDT (day 2) | secondary |
| AO 2022, US Open 2021 | Day-1 starts of 11:00 local from search summaries only | weak secondary |
| US Open 2022 | 2022 provider listings only (earliest listed start 11:00 local) | provider evidence |

- **Conclusion:** 11:00 local is the documented standard start of play. It is primary-sourced for 5 editions and
  tour-official for 1.
- **Scope:** it is **not** established for every day. Wimbledon's outside courts are guaranteed 11:00 only "on at
  least the first eight days", and later starts are possible.
- **Earlier starts:** none were found. If one occurred, it is handled without changing the rule: listings < 60 min
  after 10:00 local are `short_lead` and excluded, and actual starts at or before the decision are censored.
- **Rule unchanged:** the 10:00 local anchor and the strict 60-minute minimum listed lead.

## 4. Budget (recommended programme ceiling)

| | Credits |
|---|---|
| **Programme ceiling (base):** 143 requests × 10, assuming every request is charged | **1,430** |
| Feasibility US Open 2021 week 1 (tranche 1 + tranche 2) | 20 + 50 = 70, **included in the 1,430** |
| Contingency | 143, **only with a separate future owner approval**; never automatic |
| Remaining credits | 19,780 |

Charging: 10 per region per market; "Responses with empty data do not count towards the usage quota"
([provider docs](https://the-odds-api.com/liveapi/guides/v4/)). A charge of 0 makes the runner stop for review.

## 5. Feasibility execution (unchanged and re-verified)

- **Files:** the manifest `v2a2-feasibility-uso-2021-wk1/manifest.json` (SHA-256
  `df880fd4449dd204c8381441772af2d0c713c33a380d084716a8446d48eaceb2`) and the write-once `tranches.json`
  (`605a7eea66dc19768203a51515c31470c90805cb5303d7fed122070063311411`) are byte-identical.
- **State:** no ledger, gate or stop marker exists.
- **Code:** `stage.py` and `commands.py` are unchanged since the tranche commit; `v2_draft.py` changed only in the
  calendar loader.
- **Tranche 1:** 2 requests, at most 20 credits. Then the offline write-once gate.
- **Tranche 2:** 5 requests, at most 50 more. It needs a separate approval and a gate with status `pass`.

## 6. Exact candidate fingerprint inputs (for the owner's freeze decision)

| Input | SHA-256 at this package |
|---|---|
| `src/quantedge/reference/v2_draft_edition_calendar.csv` → `calendar_sha256` | `9f7da1467f09c0ab5c10a1cde9b36a9c84811e50cbd0605e15226351b6582484` |
| `docs/preregistration/market-benchmark-v2-CANDIDATE.yaml` (candidate text) | `8d59e5d4e5bf9e0cca2bce5018b0f12b521361b99f82e3c1b0f0c9b131433862` |
| `src/quantedge/odds/v2_draft.py` (decision, identity, tranche and audit rules) | `ba505b20cf7eac8442be0190eb85f02ead19b2b434ad642f2716814ee2eb02a7` |
| `src/quantedge/odds/stage.py` (runner, strict window, ledger) | `7c7b692b9f2952a9270c87b3ee319dcb30582144c2e406af98730228dc89c512` |
| Feasibility manifest / tranche plan | `df880fd4…aceb2` / `605a7eea…1411` (full values in §5) |
| `market-benchmark-v1` (must stay unchanged) | `e3d3f642d3dd85064136c8bf8d9fc984887d1beb84d68ad9960c92ec2467da21` |

**At freeze** (owner action, not done):
1. Copy the candidate to `configs/preregistration/market_benchmark_v2.yaml`.
2. Set `status: FROZEN` and `frozen_on`, and set `calendar_sha256` to the calendar hash above.
3. Record the resulting file's SHA-256, together with the code commit, in `docs/DECISIONS.md`.

The frozen file's hash differs from the candidate's, because those three fields change.

## 7. Owner checklist

- [ ] Accept the first/last-day evidence (§1), including the derived Wimbledon 2022 dates, or supply a verbatim
  organizer statement for them.
- [ ] Accept or reject the reporting-only waiver for rest and expected-empty days (§2).
- [ ] Accept the start-of-play evidence (§3) with its residual coverage risk.
- [ ] Confirm the programme ceiling of **1,430**. The 143 contingency needs separate future approval; the 70 for
  feasibility is part of the 1,430.
- [ ] Approve the freeze as in §6.
- [ ] Separately approve tranche 1 (20 credits). Only after a passing gate, separately approve tranche 2 (50 credits).

## 8. Commands — ONLY after the freeze and each separate approval

```
uv run quantedge odds v2-tranche-run --name v2a2-feasibility-uso-2021-wk1 --tranche 1               # dry run
uv run quantedge odds v2-tranche-run --name v2a2-feasibility-uso-2021-wk1 --tranche 1 --execute --approve-paid-calls
uv run quantedge odds build-tables --stream historical
uv run quantedge odds link --stream historical
uv run quantedge odds v2-tranche-check --edition uso-2021 --name v2a2-feasibility-uso-2021-wk1 --tranche 1
# STOP and report. Tranche 2 only with a passing gate and approval 2:
uv run quantedge odds v2-tranche-run --name v2a2-feasibility-uso-2021-wk1 --tranche 2 --execute --approve-paid-calls
uv run quantedge odds build-tables --stream historical
uv run quantedge odds link --stream historical
uv run quantedge odds v2-audit --edition uso-2021 --stages v2a2-feasibility-uso-2021-wk1
```
