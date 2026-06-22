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

Step 1 does not validate, normalize, or save realtor records.

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

## Step 2: Raw Snapshot Store

Store raw BCFSA page responses in SQLite before validation or normalization:

```bash
realtor-agent --query smith --hits-per-page 2 --store-raw
```

Store multiple raw pages:

```bash
realtor-agent --all --hits-per-page 1000 --max-pages 2 --store-raw
```

This creates:

```text
data/realtor_agent.db
  raw_snapshots
```

The `raw_snapshots` table stores:

```text
source
adapter_version
endpoint
query_params
raw_json
response_hash
fetch_status
fetched_at
```

## Step 3: Validation

Validate stored raw snapshots before normalizing or saving realtor records:

```bash
realtor-agent --validate-raw
```

Validation checks each raw realtor record for:

```text
licence number
name
brokerage name
source timestamp
malformed record shape
basic field types
```

Bad records are written to:

```text
normalization_errors
```

Each validation error also stores:

```text
validator_version
```

Good records are only counted for now. They are not inserted into a realtor table yet.

## Step 4: Normalization

Normalize valid BCFSA raw records into one standard shape:

```bash
realtor-agent --normalize
```

This creates:

```text
normalized_realtors
```

BCFSA fields are converted like this:

```text
licence_number -> license_number
business_name  -> brokerage
location       -> city
subtype        -> license_level
services       -> license_category
objectID       -> source_record_id
```

Each normalized row stores:

```text
normalizer_version
source_fetched_at
raw_snapshot_id
record_index
raw_record
```

This is still a staging table. Step 5 will save/update the final `realtors` table.

## Step 5: Save Realtors

Save normalized rows into the final current-state realtor table:

```bash
realtor-agent --save-realtors
```

This creates/updates:

```text
realtors
```

The `realtors` table stores the current realtor profile:

```text
license_number
name
brokerage
status
city
address
license_level
license_category
source
source_record_id
source_fetched_at
normalizer_version
first_seen_at
last_seen_at
updated_at
```

Records are matched by:

```text
license_number
```

If the same license number already exists, the row is updated instead of duplicated.

## Step 6: Change Detection

Change detection runs during:

```bash
realtor-agent --save-realtors
```

Before updating a realtor row, the system compares the existing `realtors` row with the
latest normalized row for that license number.

It writes changes into:

```text
change_events
```

Detected events include:

```text
new_realtor
brokerage_changed
status_changed
location_changed
profile_changed
```

Tracked fields:

```text
name
brokerage
status
city
address
license_level
license_category
```

## Step 7: Dashboard

Start the local dashboard:

```bash
realtor-agent --dashboard
```

The dashboard opens a small local web server and reads from:

```text
data/realtor_agent.db
```

Current dashboard features:

```text
search realtor
search brokerage
rows per page
previous/next pagination
view profile
view change history
view sync logs
export CSV
```

Dashboard search modes:

```text
Realtors   -> searches realtor name and licence number
Brokerages -> shows brokerage rows and searches brokerage name
```

Optional settings:

```bash
realtor-agent --dashboard --host 127.0.0.1 --port 8765
```

## Required `.env` Settings

```env
BCFSA_ALGOLIA_APP_ID=...
BCFSA_ALGOLIA_API_KEY=...
BCFSA_ALGOLIA_INDEX=...
BCFSA_ALGOLIA_FILTERS=...
```

## Next Step

After this MVP, the next step should be improving data quality checks and adding another source adapter.
