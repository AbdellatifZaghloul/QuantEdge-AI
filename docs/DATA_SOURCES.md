# QuantEdge AI — Data Sources

Status: **Phase 0.5 revision** — last verified **2026-11-01**.
Supersedes the Phase 0 version. See [DECISIONS.md](DECISIONS.md) for the decisions this document depends on.

Rules for this document:
- Every capability claim cites what was checked. Anything not confirmed on an official page is **[UNVERIFIED]**.
- Nothing here is legal advice. Where terms are ambiguous we take the most restrictive reading.
- No dataset below is assumed to support commercial use, redistribution, or publication of model weights
  unless its terms say so explicitly.

---

## 1. Licence classes

Every adapter, raw file, and derived artifact carries one licence class (ARCHITECTURE.md §7):

| Class | Meaning | Allowed uses in QuantEdge |
|---|---|---|
| `RESEARCH_NC` | Non-commercial research licence (e.g. CC BY-NC-SA 4.0) or private-use-only terms | Local research, non-commercial public portfolio with attribution; **no commercial deployment**; derived artifacts (features, ratings, model weights) inherit the class |
| `RESEARCH_PRIVATE` | Private-individual use only, no redistribution, and stated objections to model training | Local, private evaluation only; never published at row level; not used for model training (D-03) |
| `COMMERCIAL_LICENSED` | Terms explicitly permit commercial use in our intended way | Candidate foundation for a future commercial product, subject to the specific terms |
| `OWN_COLLECTED` | Data we record ourselves (e.g. our paper-trade ledger) | Ours, subject to the terms of whatever we recorded it from |

A future commercial product must be buildable from `COMMERCIAL_LICENSED` + `OWN_COLLECTED` sources
alone. Today **no such results/statistics source is in hand** — that is an open business decision (D-08),
not something the architecture can solve.

---

## 2. Source inventory

| Source | Content | Coverage (verified) | Timing | Terms (verified) | Class | Role |
|---|---|---|---|---|---|---|
| Sackmann ATP data via **`Aneeshers/tennis-sackmann-archive`** | ATP results, per-match serve stats, rankings, players | `atp_matches_*` 1968–2026; `qual_chall` 1978–2026; `futures` 1991–2026; rankings by decade + current. Latest `tourney_date` observed in 2026 file: **20260525** (Roland Garros, through the final); no grass events seen (sampled, not exhaustive) | Tournament start date only | CC BY-NC-SA 4.0 (Sackmann). Mirror adds no rights | `RESEARCH_NC` | **Primary research results + stats** |
| `JeffSackmann/tennis_atp` (original) | same | **HTTP 404** (checked 2026-11-01). No official explanation found | — | CC BY-NC-SA 4.0 | — | Unavailable; attribution source of truth |
| TML-Database (`Tennismylife/TML-Database` + stats.tennismylife.org) | ATP results + stats, ATP player IDs, updated ~daily | 1968–2026 yearly CSVs on GitHub (repo now "historical reference"); live DB on website | Tournament start date | "educational, analytical, and research purposes only"; **commercial model training and monetised model probabilities need written permission**; no SPDX licence file; may include Sackmann-derived content [UNVERIFIED] | `RESEARCH_NC` | Optional research source for post-June-2026 results (D-04) |
| Match Charting Project (`JeffSackmann/tennis_MatchChartingProject`) | Shot-by-shot points, crowdsourced | Live repo, last push 2026-09-18; README: "over 5,000 matches" | Match date | CC BY-NC-SA 4.0; README: "I'm serious about the license" | `RESEARCH_NC` | Research only; biased sample |
| Grand Slam point-by-point (archive `slam_pointbypoint/`) | Point sequences | 2011–2024; the mirror README cites upstream commit `6febb77` (abbreviated there; not pinned by us; not ingested in P1) | Per-point | CC BY-NC-SA 4.0 | `RESEARCH_NC` | Markov-assumption research only |
| **tennis-data.co.uk** | Results + bookmaker odds (W/L columns), Max/Avg | ATP results from Jan 2000, odds from 2001; season files to 2026 | **No timestamps** | Private individuals only; **not** for commercial or data-training products; **permission requests for AI/model-training are explicitly refused** | `RESEARCH_PRIVATE` | Private, untimed **benchmark** only (D-03) |
| **The Odds API** | Timestamped bookmaker odds snapshots | ATP Slams + selected ATP 1000/500 only (table §4.2); 5-min snapshots since Sept 2022 | Snapshot timestamp | Commercial use and ML training allowed if their data is not the product being sold; no resale or redistribution | `COMMERCIAL_LICENSED` (paid plan) | **Primary timestamped odds** (research and future product) |
| Betfair Exchange historic data | Exchange prices | From April 2015; Basic = 1-min last traded price, free; Advanced = 1 s, top-3 ladder, volume; Pro = 50 ms full ladder | Exchange time | Needs a Betfair.com account; not usable from unsupported countries; commercial use needs a Betfair data licence | Depends on licence | **Excluded for now**: assumed inaccessible to a US-based developer (D-07) |
| Pinnacle API | Odds | "closed for the general public since July 23rd, 2025" (official docs repo) | — | Bespoke only | — | Not available directly. Pinnacle prices still appear via The Odds API (EU region) and in tennis-data `PSW/PSL` |
| Commercial feeds (Sportradar, Stats Perform, SportsDataIO, api-tennis.com, Enetpulse, OpticOdds) | Results, stats, odds | Exist with tennis coverage (Sportradar is the ATP's official betting-data partner per trade press); depth and price [UNVERIFIED] | Varies | Enterprise/commercial licences | `COMMERCIAL_LICENSED` if bought | Future commercial path only (D-08) |

---

## 3. Results and statistics (research tier)

### 3.1 Sackmann archive — verified facts and caveats

**Pinned commit (full SHA):** `83733587353df8a41f2fd4f516147d5aa83f5a8d`, repo `Aneeshers/tennis-sackmann-archive`.
The fetch also verifies each file's git blob SHA-1 against the tree at that commit and records our own SHA-256.

Provenance:
- Mirror HEAD is `83733587353df8a41f2fd4f516147d5aa83f5a8d` (2026-06-25, single squashed commit). The repo totals about 760 MiB uncompressed.
- The README says the ATP snapshot came from "upstream commits made in June 2026", but gives **no upstream SHA**.
- There are no checksums and no statement of byte-identity with upstream.
- Hugging Face mirror: `Aneeshers/tennis-sackmann-archive`.
- Our own SHA-256 manifest becomes the provenance record.

From the upstream README and `matches_data_dictionary.txt` (read in full via the mirror):

| Field | Definition / caveat | Consequence for us |
|---|---|---|
| `tourney_date` | "usually the Monday of the tournament week" | Not a match date |
| `match_num` | "Often starting from 1, sometimes counting down from 300, and sometimes arbitrary"; 2026 file has United Cup counting down and Roland Garros counting up | **Never** used for chronology |
| `*_rank`, `*_age` | As of `tourney_date` (pre-**event**, not pre-match) | Safe pre-match; stale within event |
| Rankings | Mostly complete from 1985; 1982 missing; 1973–84 intermittent | Rank features unusable in early eras |
| Match stats | Tour level 1991+, Challengers 2008+, tour qualifying 2011+; some stats deleted after sanity checks | Coverage-aware features and missingness indicators |
| `tourney_level` | G, M, A, C, S, F, D (E, J, T reserved) | Mapping table in `reference/` |
| `round` | Observed: Q1–Q3, R128…F, RR (and others to be profiled) | Ordinal table |
| Score | Observed: `RET`, `W/O`, `Walkover`, match tiebreak `[11-9]` — **two walkover spellings** | Parser must normalise; unknown tokens quarantined |
| File order | Not sorted by date (e.g. Davis Cup rows appended at end) | Sort explicitly |

**Snapshot end.** The ATP data ends around Roland Garros 2026 (latest observed `tourney_date` 20260525;
sampled, so Phase 1 must confirm). Wimbledon 2026 onward is absent.

### 3.2 TML-Database
Same column layout plus an `indoor` column and alphanumeric ATP IDs. Terms are **stricter** than
CC BY-NC-SA for commercial purposes, and its lineage from Sackmann data is unclear. Use it only as a separate,
optional `RESEARCH_NC` adapter, mainly for post-June-2026 results, and never merge it silently with Sackmann rows.

### 3.3 Excluded
- ATP/Flashscore/OddsPortal/Sofascore scraping: terms-restricted or prohibited.
- Data committed inside third-party repos (see OPEN_SOURCE_AUDIT.md), which often redistribute tennis-data or
  scraped data in breach of the source terms.

---

## 4. Odds

### 4.1 What an odds record can and cannot prove

| Concept | Definition | Do we have it? |
|---|---|---|
| **Observed price** | A price a bookmaker displayed at a timestamp | The Odds API (timestamped), tennis-data (untimed) |
| **Executable price** | A price at which a stake of size *s* would have been accepted at time *t* | **No source provides this.** Limits, acceptance delays, price changes on submission, and account restrictions are unobserved |
| **Accepted wager** | A bet actually matched/settled | Only our own records if we ever bet manually (out of scope) |

Consequences:
- Every backtest models execution with explicit, **unverified** assumptions (latency, slippage, max stake,
  acceptance probability) and reports sensitivity to them (VALIDATION.md §7).
- "Observed" is never relabelled "executable".

### 4.2 The Odds API — official historical tennis coverage (ATP)

Source: "Earliest Historical Timestamps" table on `the-odds-api.com/historical-odds-data/`, which matches
`/sports/tennis-odds.html`. The tennis page says: *"The following competitions are currently covered: Grand Slams,
ATP 1000, ATP 500, WTA 1000 and WTA 500."*

| Tournament | Sport key | Historical data from (UTC) |
|---|---|---|
| US Open | `tennis_atp_us_open` | 2020-08-28 10:05 |
| French Open | `tennis_atp_french_open` | 2020-09-24 22:55 |
| Australian Open | `tennis_atp_aus_open_singles` | 2021-02-06 11:45 |
| Wimbledon | `tennis_atp_wimbledon` | 2021-06-25 22:35 |
| Canadian Open | `tennis_atp_canadian_open` | 2024-08-06 04:10 |
| Cincinnati Open | `tennis_atp_cincinnati_open` | 2024-08-12 01:50 |
| China Open | `tennis_atp_china_open` | 2024-09-24 19:25 |
| Shanghai Masters | `tennis_atp_shanghai_masters` | 2024-10-05 03:55 |
| Paris Masters | `tennis_atp_paris_masters` | 2024-10-28 04:10 |
| Qatar Open | `tennis_atp_qatar_open` | 2025-02-16 10:00 |
| Dubai Championships | `tennis_atp_dubai` | 2025-02-24 01:15 |
| Indian Wells | `tennis_atp_indian_wells` | 2025-03-04 22:20 |
| Miami Open | `tennis_atp_miami_open` | 2025-03-17 21:55 |
| Monte-Carlo Masters | `tennis_atp_monte_carlo_masters` | 2025-04-06 09:30 |
| Madrid Open | `tennis_atp_madrid_open` | 2025-04-22 05:55 |
| Italian Open | `tennis_atp_italian_open` | 2025-05-06 10:45 |
| Barcelona Open | `tennis_atp_barcelona_open` | 2026-04-13 13:05 |
| Munich | `tennis_atp_munich` | 2026-04-13 13:05 |
| Hamburg Open | `tennis_atp_hamburg_open` | 2026-05-17 03:30 |
| Halle Open | `tennis_atp_halle_open` | 2026-06-15 03:45 |
| Queen's Club | `tennis_atp_queens_club_champ` | 2026-06-15 03:45 |
| Washington Open | `tennis_atp_washington_open` | 2026-07-27 01:40 |
| Japan Open | `tennis_atp_japan_open` | 2026-09-29 05:30 |
| Swiss Indoors Basel | `tennis_atp_swiss_indoors` | 2026-10-26 05:05 |
| Vienna Open | `tennis_atp_vienna_open` | 2026-10-26 05:05 |

**Known limitations (verified unless marked):**
1. **Not covered:** ATP 250, Challenger, ITF, and the ATP Finals. Some ATP 500s (e.g. Rotterdam, Rio, Acapulco) are
   absent from the table.
2. Sport keys are per tournament and *"Data will only be returned if the tournament is in season."*
3. *"Historical data will only be available from the time that coverage is added for a sport, bookmaker or market."*
   Most Masters start late 2024 or 2025, and most 500s start in 2026.
4. Snapshots: featured markets from 2020-06-06 at 10-min intervals, 5-min from Sept 2022. The historical endpoint
   *"will return the closest snapshot equal to or earlier than the provided date parameter."*
5. **Markets:** *"Match winner odds are mainly covered at this time. Limited coverage is also available for game
   spreads and game totals (over/under) from selected bookmakers."* Tennis-valid additional markets are listed
   (`h2h_s1`, `spreads_s1`, `totals_s1`, `alternate_set_spreads`, …), but *"Additional markets are currently limited to
   US sports and selected bookmakers."* Actual historical depth for tennis totals and handicaps is
   **[UNVERIFIED — requires a paid probe]**.
6. Which bookmakers price each ATP event is **[UNVERIFIED]**. Pinnacle and Betfair exchange keys exist in the EU/UK
   regions.
7. *"Prior to September 18th 2022, only decimal odds were captured."* (benign for us)
8. *"Data errors aren't common but they can occur … they can still be present in historical snapshots."*
9. Snapshots are "bookmaker odds as they were at a point in time". They are not accepted wagers.
10. Quota: historical = *"10 per region per market"* per call. Plans with historical access start at $30/month
    (20K credits). The free tier has no historical data.

**Implications:**
- Timestamped (Tier B) historical evidence covers only the Slams from 2020–21 and a few Masters from late 2024.
  Most of it falls inside the 2025+ sealed holdout (D-05).
- Pricing of totals and handicaps is **not testable** against timestamped history until a probe shows coverage.
- Cost arithmetic: one region × one market × hourly snapshots over a 14-day Slam = 336 calls × 10 = 3,360 credits.
  Budget per decision D-06.

### 4.3 tennis-data.co.uk

Verbatim from the site:
- Homepage: *"its use is intended for private individuals only, NOT commerical or data training products using
  automated bots/scrapers/AI."*
- `data.php`: *"Tennis-Data maintains full copyright over the data files in their spreadsheet format."*
- `contact.php`: *"You should not ask for permission to use the data for these purposes. Any email requests asking
  for permission to use the data for these purposes will be ignored."* (AI/model training with a view to
  commercialisation)
- `notes.txt`: odds *"generally represent the most recent before play starts, as reported by oddsportal.com and
  the individual bookmakers."*

Our policy (D-03):
- **No permission request** will be sent.
- Files are downloaded **manually** by the developer. There is no automated fetching.
- The data is used **only** as a private, untimed evaluation benchmark: de-vigged near-close probabilities to score
  calibration against, and closing-line references.
- It is **never** used as a training feature or calibration target, and never committed, published at row level,
  or included in any artifact that leaves the machine.
- Public outputs may state only aggregate comparison metrics, labelled Tier C. Whether even that is acceptable is
  a judgement call; the conservative default is to keep Tier C results out of the public portfolio (D-03).
- `Max*` columns are a cross-book maximum and are never treated as a single obtainable price.
- The timing is "generally" pre-start and unstamped, so it is never treated as obtainable at any earlier time.

### 4.4 Betfair historic data

Verified via Betfair developer-support articles and the Betfair Automation Hub:
- Data available from April 2015, released 5 days after settlement.
- Tennis "Game betting" markets are not in the historical data.
- Tennis Advanced/Pro subscriptions are paid (currency [UNVERIFIED]).
- Access requires a Betfair.com account; *"If Betfair.com account registration isn't supported for your country
  you will not be able to access the Betfair Historical Data website."*
- Commercial use requires a separate Betfair data licence.

Excluded unless D-07 changes.

---

## 5. Point-level data

Slam PBP (2011–2024) and the MCP are `RESEARCH_NC`. They are used to test the iid-point and serve-rotation
assumptions in the Markov model, never as pre-match features. MCP charting selection is non-random
(it skews toward famous players and big matches).

---

## 6. Strategy

### 6.1 Now: non-commercial research
- **Results/stats:** Sackmann archive at a pinned mirror commit, verified by our own SHA-256 manifest.
- **Odds, timestamped:** The Odds API, after a paid probe confirms tennis bookmaker and market depth (D-06).
  Historical pulls for covered ATP events, plus **forward capture starting as early as possible**, because
  timestamped data cannot be recovered retroactively.
- **Odds, untimed:** tennis-data, private benchmark only (D-03).
- **Post-June-2026 results:** deferred. Options are TML (research only), our own result capture for
  odds-covered events, or a commercial feed (D-04).

### 6.2 Later: possible commercialisation (not assumed)
- Adapters are separated by licence class (ARCHITECTURE.md §7). Swapping in a commercial results feed means
  writing a new adapter that emits the same canonical schema.
- Everything derived from `RESEARCH_NC` data (ratings, features, **model weights**, calibration maps, reports) is
  tainted and cannot ship in a commercial build. A commercial product would need retraining from scratch on
  commercially licensed data.
- The Odds API terms permit commercial ML use if their data is not the product resold. Any commercial launch
  still needs legal review and a fresh check of the terms.
- Commercial feeds (Sportradar et al.) are enterprise-priced [UNVERIFIED]. They are a business decision (D-08).

---

## 7. Credentials, storage, attribution

- API keys live only in `.env` (gitignored). `.env.example` has empty values. A pre-commit secret scan runs.
- `data/` is gitignored and namespaced by licence: `data/raw/research_nc/…`, `data/raw/research_private/…`,
  `data/raw/commercial/…`.
- CI uses only synthetic fixtures written by us.
- Attribution text for README and dashboard: *"Match results and statistics: Jeff Sackmann / Tennis Abstract,
  CC BY-NC-SA 4.0, obtained via the tennis-sackmann-archive mirror; modified (canonicalised). Odds snapshots:
  The Odds API."* Tennis-data is not shown publicly (D-03).

## 8. Sources checked (2026-11-01)

- github.com/Aneeshers/tennis-sackmann-archive: README, LICENSE, file tree, `atp/UPSTREAM_README.md`,
  `atp/matches_data_dictionary.txt`, byte windows of `atp/atp_matches_2026.csv`; Hugging Face mirror page
- github.com/JeffSackmann (repo list), github.com/JeffSackmann/tennis_MatchChartingProject
- github.com/MLT-OSS/FirstData issue #238, PR #239
- github.com/Tennismylife/TML-Database
- the-odds-api.com: `/historical-odds-data/`, `/sports/tennis-odds.html`, `/sports-odds-data/sports-apis.html`,
  `/sports-odds-data/betting-markets.html`, `/sports-odds-data/bookmaker-apis.html`, `/liveapi/guides/v4/`,
  `/terms-and-conditions.html`, the homepage plans section
- tennis-data.co.uk: homepage, `/data.php`, `/notes.txt`, `/contact.php`, `/disclaimer.php`
- support.developer.betfair.com articles 360002407732, 360002427051, 360008664937, 30553823020444,
  360017615137, 360019984158, 360002464152, 360002190752, 28271961503516;
  betfair-datascientists.github.io/data/usingHistoricDataSite/
- github.com/pinnacleapi/pinnacleapi-documentation
- developer.sportradar.com/tennis, sportsdata.io/tennis-api, api-tennis.com, enetpulse.com, opticodds.com
