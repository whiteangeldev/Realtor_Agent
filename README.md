# Realtor Agent

Simple realtor data pipeline, built one step at a time.

## MVP Flow

```text
BCFSA Adapter
  -> Raw Snapshot
  -> Validation
  -> Normalizer
  -> Realtors Table
  -> Change Events
```

## Step 1: Fetch Data

Current code only does this:

```text
BCFSA Algolia API -> Source Adapter Layer -> raw JSON
```

No database yet. No validation yet. No dashboard yet.

Current source adapter:

```text
source_adapters/base.py
  -> SourceAdapter contract

source_adapters/bcfsa_algolia.py
  -> BCFSAAlgoliaAdapter
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

The BCFSA API settings live in `.env`.

## Fetch A Sample Page

```bash
realtor-agent --query smith --hits-per-page 2
```

Save the raw API response to a file:

```bash
realtor-agent --query smith --hits-per-page 2 --output data/bcfsa_raw_sample.json
```

## Fetch All Realtor Search Records

Use an empty query and `--all`:

```bash
realtor-agent --all --hits-per-page 1000 --output data/bcfsa_all_raw.json
```

For a small test run, add a page limit:

```bash
realtor-agent --all --hits-per-page 1000 --max-pages 2 --output data/bcfsa_first_2_pages.json
```

This still stores raw API data only. Step 2 will save this into `raw_snapshots`.

## Required `.env` Settings

```env
BCFSA_ALGOLIA_APP_ID=...
BCFSA_ALGOLIA_API_KEY=...
BCFSA_ALGOLIA_INDEX=...
BCFSA_ALGOLIA_FILTERS=...
```

## Next Step

Step 2 will store this raw JSON into a `raw_snapshots` table.
