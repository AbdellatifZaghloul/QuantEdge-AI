# Stage B US Open 2022 — NO-GO root-cause audit (outcome-blind)

Date: 2026-11-02.

**Scope:**
- Offline only: sockets disabled in every script. **0 network calls, 0 credits.**
- RG and AO Stage B not executed. No `pilot-compare`.
- No outcomes, scores or model metrics. No 2023–24 or 2025+ rows: identity work uses the inner-fold view only.
- `market-benchmark-v1` unchanged (SHA-256 `e3d3f642…67da21`).
- Raw store, 4 manifests and 2 ledgers byte-identical before and after (29 files re-hashed).
- Aggregate only. Planned events are labelled `U01`–`U48` in decision order. No provider IDs, player names or prices
  appear here.

**Legend:** **[F]** confirmed fact from stored data; **[H]** hypothesis or interpretation.

## 1. Integrity and spend [F]

| Check | Result |
|---|---|
| Capture log / verified bodies | 22 / 22 records (4 probe + 9 Stage A + 9 US Open); every SHA-256 matches; 22 distinct bodies |
| US Open ledger vs manifest (`9d26c49b…`) | 9 / 9 entries, same IDs and dates; every returned snapshot as planned (13:55 … 08-31 13:55) |
| Credits | 10 per call, cumulative **90**; quota 19,870 → **19,780** in exact steps; Stage A ledger unchanged |
| Event presence | 47 / 48 (request 09: 15 / 16) |
| Rebuilt tables | Identical to the owner's: 213 events, 16,618 raw, 16,553 valid, 65 quarantined (`price_not_above_one`) |
| Audit reproduced | GO = false; 23 / 48 available; gating median 49.64; available-only 10.25; 24 moved, all unavailable |

## 2. Why US Open failed and Wimbledon passed

| | Wimbledon (Stage A) | US Open (Stage B) |
|---|---|---|
| Discovery-snapshot starts [F] | Court-order estimates (many distinct minute values) | **Session placeholders: 5 on-the-hour times** (15:00, 17:00, 19:00, 21:00, 01:00) |
| Requests [F] | 9 over 22 h, often 10–30 min apart | 9, mostly hourly or wider |
| Planned decision − snapshot [F] | 0–15 min | **5 min** for every group |
| Moved anchors [F] | 19 / 39 | 24 / 48 |
| Moved events still available [F] | **14 / 19**, rescued by neighbouring snapshots bought for other groups | **0 / 24**; nearest earlier snapshot 39–123 min before the frozen decision |
| Unmoved events available [F] | 20 / 20 | 23 / 24 (the exception is U41, §5) |

**Mechanism [F]:**
- Later snapshots replaced the placeholders with "followed by" estimates **8–80 min earlier**.
- The frozen anchor (earliest start ever observed − 60) moved every such decision before the snapshot bought for it,
  which was only 5 min early. That snapshot became inadmissible.
- The previous snapshot was an hour or more older, so the quote was stale.
- Session openers (unmoved, started on the hour) are almost all available at a median age of 10.25 min.

## 3. The 24 moved-anchor events (timestamp evidence only)

All 24 were first seen in the 11:55 discovery snapshot.

| Group (planned start) | Events | Earliest observed start | Shift (min) | Frozen decision | Newest admissible snapshot | Its age at decision (min) |
|---|---|---|---|---|---|---|
| 17:00 | U07–U14 (8) | 16:28–16:49 | −11 to −31 | 15:28–15:49 | 14:55 | 39–59 |
| 19:00 | U15–U24 (10) | 18:22–18:49 | −11 to −38 | 17:22–17:49 | 15:55 | 93–120 |
| 21:00 | U25–U29 (5) | 19:40–20:50 | −10 to −80 | 18:40–19:50 | 17:55 | 50–120 |
| 01:00 (+1 d) | U32 (1) | 00:52 | −8 | 23:52 | 21:55 | 123 |

- **Valid pre-decision source timestamps** (`last_update`): earliest 11:47 for all groups. Latest 14:49 (17:00
  group), 15:49 (19:00), 17:50 (21:00) and 21:48 (01:00).
- Each event's full start-version history (2–9 versions) is in the offline working files and is not reproduced here.

**What the start versions are** (378 versions across the 48 planned events) [F]:

| Kind | Versions | Meaning [H] |
|---|---|---|
| On-the-hour placeholder | 239 | Session or block start ("not before") |
| Round-minute estimate, before that time | 88 | Order-of-play estimate for a "followed by" match, revised as earlier matches progress |
| Non-zero seconds, observed at or after that time | 51 | Actual-start stamp written once the match is in play |

For the 24 moved events, the anchor-setting (earliest) version is:
- a **pre-match estimate in 21** cases;
- an **in-play actual-start stamp in 3** (U09, U10, U12): the match began earlier than any estimate.

Across the store, in-play stamps set the earliest start for 11 of 72 Wimbledon events and 3 of 69 US Open events.

No version is internally inconsistent, and no start was repaired, merged or imputed.

**Is the frozen anchor still conservative? Yes** [F]:
- every admissible quote satisfies source ≤ observation ≤ decision ≤ every start version − 60 min;
- a moved anchor only removes quotes, never adds them;
- there are no timestamp violations (c4).

## 4. Is the frozen anchor acquisition-dependent? Yes, and planned collection is systematically too late [F]

1. **Monotone in acquisition.** The anchor is a minimum over every stored snapshot. Each additional snapshot can only
   keep a decision or move it earlier.
2. **Self-invalidation.** For **14 / 24**, the event's *own* decision-time snapshot shows the earlier start that puts
   the frozen decision before that snapshot. With only discovery and that one snapshot, **15 / 24** would still move.
3. **Real-time knowability.** The anchor-setting version was visible before the frozen decision for **15 / 24** US
   Open events and **13 / 19** Wimbledon events. For the other **9 and 6**, it was first seen after the decision.
   That includes in-play stamps and self-invalidating snapshots.
   - No collector working forward in time could have targeted these.
   - Targeting them historically requires choosing acquisition times with information from after the decision. That
     is outcome-blind but hindsight-driven.
4. **No fixed request lead works.** The table models requesting X minutes earlier than planned with the same number
   of calls. It counts own-group snapshots inside the 30-min window, given 6-min lag. It shows schedule feasibility
   only, not quote availability.

| Lead X | Wimbledon (39) | US Open (48) |
|---|---|---|
| 0 (as run) | 22 | 24 |
| 10–15 min | 20–21 | **36** |
| 20–25 min | 3–5 | 20 |

A lead tuned to US Open harms Wimbledon. Choosing one from these data would be fitting to two tournaments.

## 5. The 16 `duplicate_provider_event` rows — provider duplicates, not a linker defect

| | Before US Open acquisition | After |
|---|---|---|
| Rows (canonical matches) | 2 (1, Wimbledon) | **16 (8)**: 1 Wimbledon + **7 US Open** |

**Facts [F]:**
- Each duplicate is **two provider IDs for the same player pair**, both linking to one canonical match. In US Open
  cases one ID shows the next day's session time and the other the following day's.
- In **6 of 7** US Open cases, both IDs were live **in the same snapshot**: concurrent duplicate listings. The older
  listing then disappears and the newer one persists.
- In the Wimbledon case the IDs never coexist: one replaced the other.
- These listings entered the store because the new US Open snapshots extend into future rounds.

**Interpretation:** the provider changes event identity as next-round days are fixed [H]. The linker behaves as
designed: it refuses to choose between claimants and keeps both with an explicit status. **Nothing was merged or
discarded.**

**Effect on the pilot [F]:**
- 0 of the 16 claimant rows has an available quote at its frozen decision. The current usable population is
  unchanged.
- One planned event is affected (**U41**, §6).
- Risk for full-edition stages [H]: future-round listings will produce more duplicates. Resolving them would need a
  pre-specified point-in-time identity rule (e.g. the claimant live in the latest snapshot ≤ decision). That is a
  protocol-level choice under `require_link_status: linked`, not something to apply now.

## 6. The planned event absent from request 09 [F]

- **U41** (listed for 08-31 15:00) was last observed at 08-30 15:55.
- From 08-30 17:55, a different provider ID for the same pair appears with a later start (09-01 00:15 UTC), so its
  ID was replaced (§5).
- Admissibility: its frozen decision is 08-31 14:00, and its newest own quote is ~22 h older, so it is `stale`. It is
  also unlinked (`duplicate_provider_event`).
- The replacement ID's quotes belong to another provider event with a different start. Using them would be a
  hindsight merge, so they were **not** attached. The absence cannot make any quote admissible or inadmissible
  wrongly.

## 7. US Open usable-and-linked population (exact intersection) [F]

| Condition (cumulative) | Events |
|---|---|
| Planned | 48 |
| Uniquely linked | 47 |
| … complete pre-match two-way h2h observed | 47 |
| … available at the frozen decision, ≤ 30-min freshness | 23 |
| … ≥ 2 genuinely distinct price sources | **23** (books 6–12, distinct price pairs 5–11) |

- **Distinct sources:** sport888 = unibet again (identical on 22/22). Distinctness is counted on price pairs.
- **Pinnacle:** available on **8 / 23 (35 %)**, far below the 80 % adequacy threshold. betonlineag is available on
  23/23 (diagnostic only; the reference is decided on the full pilot population).
- **Pilot to date** (planned events, ≥ 2 distinct sources): Wimbledon 33 + US Open 23 = **56**.
- **Composition bias [H]:** the 23 are essentially the session openers. Followed-by matches are absent, so the
  population is not representative of the draw.

## 8. Gating median 49.64 min vs available-only 10.25 min [F]

- **The populations:** the gate (c3) uses each book's newest admissible complete quote, available or stale. That was
  pre-specified in the Stage A preflight §7, recorded in D-33, and is unchanged.
- **Why the median is high:** at the US Open, **263 of 496** such rows are stale (the moved events, median age 107
  min). The median therefore falls in the stale part.
- **Available-only:** that population is the 233 fresh rows, all from unmoved events (median 10.25, max 11.38).
- **Implementation:** verified as intended by the synthetic tests added in the Stage A review.
- **Nothing changed after the results:** no threshold or population was altered. Under any of the three populations,
  c1 (48 % < 80 %) fails on its own, so the NO-GO does not depend on the age definition.

## 9. Hypothetical rescue schedule (offline model; nothing requested; no quote assumed)

Targeting today's frozen decisions of the 24 moved events, with the same planner and existing snapshots excluded:

| Gap bound | Extra requests | Extra credits | Snapshot times (UTC, 08-30) |
|---|---|---|---|
| 15 min | 7 | 70 | 15:25, 15:35, 17:15, 17:35, 18:35, 19:35, 23:45 |
| 10 min | 10 | 100 | 15:25, 15:35, 15:45, 17:15, 17:35, 17:45, 18:35, 19:35, 19:45, 23:45 |

These would at most supply snapshots inside the window. That is a necessary condition for availability, not
availability itself. Caveats:
1. The targets come from information after the decision (9 of 24).
2. Each new snapshot can reveal an even earlier start and move anchors again (monotone; convergence not guaranteed).
3. Buying them would retroactively change the defined population of an already-audited stage.

## 10. Is the Stage B–D strategy valid and efficient?

1. **Valid but acquisition-defined.**
   - Under `market-benchmark-v1` every admissible quote is genuinely pre-decision.
   - But the population (which events have a decision-time quote) depends on how many snapshots are bought and when.
   - Planning from a discovery snapshot is too late by construction for followed-by matches, and later purchases undo
     earlier ones.
   - The confirmatory population would be set by an unregistered, data-dependent acquisition path. That is
     outcome-blind, but scientifically weak, and biased towards session openers.
2. **Inefficient.**
   - Covering followed-by matches robustly needs dense uniform cadence through each play day. [H] At 20-min cadence
     that is ~39 snapshots per US Open-length day, ≈ 390 credits per day and ≈ 5,000 per edition.
   - Ten editions (≈ 50,000 credits) exceed the 19,780 remaining.
   - Hindsight-targeted iteration is cheaper per round but unbounded in rounds.
3. **Identity drift grows with coverage.** Full-edition stages will add duplicate listings that the frozen linking rule
   excludes.
4. **RG and AO Stage B manifests** use the same discovery-only planning and would likely fail the same way at
   placeholder-scheduled sessions [H].

## 11. Root causes and impact

| Cause | Type | Impact at the US Open |
|---|---|---|
| Frozen earliest-start anchor reacts to placeholder → estimate → in-play revisions, including after the decision | Inherent to provider scheduling under the frozen definition | 24 moved; 9 not real-time knowable |
| Plan built only from the discovery snapshot's placeholders, requests 5 min before planned decision, sparse grid | Acquisition-schedule inefficiency | 0 / 24 moved events rescued (vs 14 / 19 at Wimbledon) |
| Provider event IDs replaced or duplicated for future-round listings | Provider identity drift | 1 planned event lost (U41); 7 matches blocked, none currently usable |
| Program defect | **None found.** Anchor, selector, audit metrics and linker behave as specified; outputs reproduce exactly | — |

## 12. Implications for `market-benchmark-v1`

- **v1 remains frozen and valid as a point-in-time rule.** Its anchor makes the confirmatory population
  acquisition-dependent and partly reachable only with hindsight-targeted purchases.
- **Coverage so far:** 56 usable events from 2 of 10 editions. Pinnacle adequacy is 100 % at Wimbledon and 35 % at the
  US Open, so the reference choice is likely to fall to the fallback.
- **Proceeding:** continuing under v1 would spend most of the remaining budget for a population skewed towards session
  openers, with no guarantee of reaching the pre-registered adequacy.

## 13. Recommendation: NEW PROTOCOL REQUIRED (no further spending under v1)

**Why not the other options:**
- Acquisition redesign alone cannot make v1's anchor independent of acquisition.
- STOP would discard a fixable design.

A successor protocol must be written and frozen **before any outcome is accessed**. Candidate design
choices for the owner (none adopted):
- **Point-in-time anchor:** the start shown in the latest snapshot at or before a fixed pre-decision time, instead of
  the minimum over all snapshots.
- **Session or day anchor:** one decision per session or day (e.g. the earliest listed session start − 60 min, taken
  from a pre-decision snapshot).
  - Robust to followed-by revisions.
  - About one odds snapshot per session or day.
  - Consistent with the day-bound model cutoff (the market never has less information than the model).
- **Pre-specified identity rule** for concurrent duplicate listings.
- **Pre-specified acquisition procedure and acquisition lock**, as part of the protocol.

**Next steps (outcome-blind, zero automatic spending):**
1. Owner decides between drafting `market-benchmark-v2` and closing the market benchmark at its current scope.
2. If v2: draft it offline. Replay its anchor and acquisition rule on the 22 stored captures (no new data) to estimate
   coverage and cost. Freeze it with a new SHA-256.
3. Only then propose a capped feasibility acquisition for explicit approval.
4. Keep RG and AO Stage B suspended. Keep `pilot-compare` blocked.

**Credits: spend none of the remaining 19,780 now.**
