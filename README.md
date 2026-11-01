# QuantEdge AI

Research platform for ATP men's singles: calibrated pre-match probabilities, market comparison and
risk-aware *paper* staking analysis. **Research use only. No automated or real-money betting.**

Status: **Phase 3B — pilot preparation and pre-registration** (offline credit planner, timing
hardening, frozen market-benchmark protocol; no odds acquired, no paid calls, no betting). Phase 2 Elo results:
[docs/reviews/phase-2.md](docs/reviews/phase-2.md); odds: [docs/reviews/phase-3a.md](docs/reviews/phase-3a.md).
See [docs/ROADMAP.md](docs/ROADMAP.md).

## What Phase 1 provides

- Pinned, verified ingestion of the Sackmann ATP research data, fixed to a full 40-character commit of the
  archival mirror. Each file is checked against its git blob SHA-1 and its SHA-256 is recorded.
- Canonical Parquet tables: `players`, `tournaments`, `matches`, `match_entry`, `match_stats`, `rankings`,
  `rejected_rows`. Every input row is either accepted or rejected with a reason.
- Identifiers and player A/B orientation derived from the unordered player pair and event context only,
  so they never depend on the winner. Results are deterministic across machines.
- Conservative chronology fields and a point-in-time information-set engine
  ([docs/ARCHITECTURE.md §5](docs/ARCHITECTURE.md)).
- A development/holdout guard: 2025+ rows are ingested and schema-validated, but development views
  cannot read them.
- JSON and Markdown data-quality reports.

## Quick start

```bash
uv sync --locked                 # Python 3.12 + locked dependencies
uv run quantedge data fetch      # download + verify the pinned snapshot into data/ (gitignored)
uv run quantedge data verify     # offline re-verification of every file hash
uv run quantedge data build      # canonical Parquet tables -> data/processed/<build_id>/
uv run quantedge data qa         # qa_report.json / qa_report.md for the latest build
uv run quantedge data audit      # Gate 0 eligibility + anomaly audit (build_dir/derived/)
uv run quantedge experiment run  # Elo vs ranking baseline, nested walk-forward 2013-2022 -> runs/
uv run quantedge odds coverage   # offline odds coverage and credit estimate (no network)
uv run quantedge odds plan       # dry-run acquisition plan: zero network calls, zero credits
uv run pytest -m "not local"     # synthetic-fixture tests (what CI runs)
uv run pytest                    # also runs real-data tests when data/ is present
```

## Data, licensing and attribution

- Raw and derived research data live only under `data/`, which is gitignored and never committed.
  This repository contains code, documentation, **synthetic** test fixtures and aggregate result
  figures (with attribution) only; no row-level research data.
- Match results and statistics: Jeff Sackmann / Tennis Abstract, licensed
  [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/), obtained via the
  [tennis-sackmann-archive](https://github.com/Aneeshers/tennis-sackmann-archive) mirror and modified
  (canonicalised) here. The data and anything derived from it are **non-commercial**.
- Licence classes and lineage are tracked per artifact. A `commercial` build profile refuses
  research-derived artifacts ([docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)).
- No licence has yet been chosen for this repository's own code (see [docs/DECISIONS.md](docs/DECISIONS.md)).

## Documentation

[Architecture](docs/ARCHITECTURE.md) · [Data sources](docs/DATA_SOURCES.md) ·
[Validation](docs/VALIDATION.md) · [Roadmap](docs/ROADMAP.md) · [Decisions](docs/DECISIONS.md) ·
[Open-source audit](docs/OPEN_SOURCE_AUDIT.md) · [Phase 1 review](docs/reviews/phase-1.md) ·
[Phase 2 review](docs/reviews/phase-2.md) · [Phase 3A review](docs/reviews/phase-3a.md) ·
[Phase 3B review](docs/reviews/phase-3b.md) · [Pre-registration](docs/preregistration/market-benchmark-v1.md)
