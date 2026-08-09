# sam-it-notices

A daily pipeline that downloads SAM.gov solicitation attachments, extracts the
text, runs regex + GPT signal passes, and renders a filterable notice browser
at `sam-it-notices.vercel.app`.

## NAICS scope

Two profiles, both expressed as a NAICS list passed to `--naics-prefix`:

- **Daily cron (narrow, 4 codes)** — keeps quota use under 10 API calls/day:
  `541511 541512 541519 518210`
- **Manual backfill / wide ad-hoc (full 11 codes)** — workflow_dispatch default:
  `541511 541512 541513 541519 518210 541330 541611 541618 541690 541715 541990`

NAICS list ↔ chunk-completion state are tied: `completed_chunks.json` keys are
`(ncode, chunk_from, chunk_to)`, so adding a new NAICS code to the list will
trigger fresh queries for that code without re-doing already-drained chunks
for the existing codes. `processed.json` provides per-noticeId dedup as a
second backstop.

## Pipeline (`rfp_text_pipeline.py`)

**Bulk-search mode.** One call to `opportunities/v2/search` returns up to 1,000
opportunities *with their resourceLinks attached*, so a full daily / weekly
catch typically needs just a handful of API calls.

Flow:

1. Load state from R2: `processed.json` (noticeIds bundled) +
   `last_fetched_date.json` (cursor).
2. Set the posted-date window: from `last_fetched_date - 1 day` to today.
   First run falls back to `--start-date` or 180 days back.
3. Paginate `opportunities/v2/search?postedFrom=&postedTo=&limit=1000&offset=`
   until a short page. Each page = 1 API call.
4. For every opp in the window:
   - Skip if NAICS prefix doesn't match (default: `541`).
   - Skip if notice type isn't in the RFP-adjacent set.
   - Skip if noticeId already in `processed`.
   - Download each `resourceLink` (free S3), extract text via pypdf /
     python-docx / openpyxl, run the regex label classifier, write
     `bundles/{noticeId}.json`.
5. On clean drain, advance `last_fetched_date = posted_to` and clear
   `scan_cursor`. On 429 or page cap, save `scan_cursor.json =
   {posted_from, posted_to, offset}` so the *next* run resumes mid-drain.
6. Push state + bundles to R2 (prefix `it_rfps/`).

**Labels (regex-only for now):**
Every bundle carries `labels.{mentions_rtm, shall_count, has_agile_vocab,
has_user_vocab}` computed from `attachments[].text + metadata.description`.

**Notice types kept** (Award Notice deliberately skipped — metadata is enough):
Solicitation, Combined Synopsis/Solicitation, Sources Sought, Presolicitation,
Special Notice, Justification, Fair Opportunity / Limited Sources Justification.

**State on R2** (prefix `it_rfps/`):
- `state/processed.json` — noticeIds already bundled
- `state/last_fetched_date.json` — date cursor
- `state/scan_cursor.json` — present only mid-drain; pins window + offset
- `state/quota.json` — last run's stats
- `bundles/{noticeId}.json` — per-notice extracted text + signals
- `personnel/{noticeId}.json` — GPT-extracted personnel roles + LCATs

Extraction coverage on sample bundles: ~76% of attachments via
pypdf/python-docx/openpyxl. XLSX preserves CLIN-level pricing. Misses are
image-only PDFs (no OCR yet).

Run locally:
```bash
python3 rfp_text_pipeline.py --dry-run                   # probe first page only
python3 rfp_text_pipeline.py --start-date 2025-10-01     # bootstrap a 6mo window
python3 rfp_text_pipeline.py                              # daily incremental
python3 rfp_text_pipeline.py --max-api-calls 5            # bound a single run
```

## Build dashboard (`build_rfp_signals.py`)

Reads bundle + personnel JSON from R2, aggregates, deduplicates by
solicitation number, and writes the only two files the dashboard reads:

- `web/data/rfp_signals.json` — per-notice summary signals
- `web/data/rfp_bundles/` — full bundle payload (snippets, labels, set-aside,
  NAICS, search_text, lcats, personnel, ui_link, …), **sharded**:
  `manifest.json` lists `shard-NNN.json` files, each packed to a 4 MiB budget.

Commit `web/data/` after running — these are what the host serves.

### Why the bundles are sharded

Cloudflare Pages hard-rejects any single file over 25 MiB (26,214,400 bytes).
The old monolithic `web/data/rfp_bundles.json` reached 25,057,780 bytes —
95.6% of the limit — and the daily pipeline only ever appends, so the deploy
was weeks from failing outright. `rfp_bundle_shards.py` is the shared
writer/reader; `tests/data/test_web_file_sizes.py` fails the build if any file
under `web/` crosses 80% of the limit.

Shards are packed to a **size budget**, not a fixed count, so the number of
files grows with the corpus and no single file drifts toward the limit.

## Personnel extraction (`extract_personnel.py`)

GPT-4o-mini extracts personnel roles, education, certifications, years of
experience, and labor-category rate tables from new bundles. Results cached
to R2 at `it_rfps/personnel/{noticeId}.json` so it's idempotent.

## Dashboard (`web/index.html`)

Static site deployable to Vercel (root → `web/`). `vercel.json` routes
`/ → web/`. Uses Chart.js v4 (CDN) + DataTables for the notice browser.

`rfp_signals.json` plus the `rfp_bundles/` shards are the only data the page
fetches — no contracts, eval-method, tradeoff, SAM-vendor or protest data is
consumed by the UI. `loadBundles()` reads `data/rfp_bundles/manifest.json`,
fetches every shard in parallel, and concatenates them into one array. All
shards load up front deliberately: full-text search runs client-side over
`search_text` in the hidden `_text` column, so the corpus must be complete
before the DataTable is built — lazy-loading it would break search.

### Filters

- Chip-style two-step modal (field → value picker)
- Multiselect: notice type, department, NAICS, set-aside, label, data
- Text: title, full-text search across attachment text
- Date range: posted date
- All chip state is reflected in the URL as repeated query params
  (e.g. `?dept=DEPT+OF+DEFENSE&dept=DOE&naics=541512`). Comma is **not** a
  separator — values can contain commas (federal department names always do).
- Filtering is implemented via a single `$.fn.dataTable.ext.search` plugin
  that reads from the `rowData` argument (NOT `searchData`, which DataTables
  zeros out for `searchable: false` columns). Every column in `RFP_COLS`
  defaults to `searchable: true` for this reason.

## Files

```
rfp_text_pipeline.py       — daily SAM.gov ingest → R2 bundles
extract_personnel.py       — GPT personnel extraction → R2 personnel cache
build_rfp_signals.py       — R2 → web/data/rfp_signals.json + rfp_bundles/ shards
rfp_bundle_shards.py       — shard writer/reader shared by the build + tests
r2_sync.py                 — R2 helper module used by rfp_text_pipeline.py
web/index.html             — static dashboard (DataTables notice browser)
web/shared/filters.js      — FilterManager class
web/shared/shared.css      — design tokens + component styles
web/data/rfp_signals.json  — committed dashboard data
web/data/rfp_bundles/      — committed bundle shards + manifest.json
.github/workflows/
  rfp_text.yml             — daily 00:05 UTC: pipeline → personnel → rebuild
                              JSONs → run data + frontend tests → commit + push
tests/
  data/                    — data-integrity tests (no_drops, baseline floors)
  frontend/                — Playwright tests (page loads, filters, cards)
.env                       — SAM_API_KEY, OPENAI_API_KEY, CF_R2_* (gitignored)
vercel.json                — routes / → web/
```

## CI gating

`rfp_text.yml` runs both test suites *before* the commit-and-push step. If
either suite fails, no commit lands and the previous build keeps serving.

- `tests/data/` — schema/floor checks on `web/data/`, plus
  `test_web_file_sizes.py`, which fails if any file under `web/` passes 80% of
  the Cloudflare Pages 25 MiB per-file limit
- `tests/frontend/` — Playwright + chromium against a local `http.server`,
  including a parametrized test that asserts every selectable filter option
  yields at least one row (regression cover for two recent filter bugs)
