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
is_currently_found
removed_at
last_seen_run_id
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
removed_realtor
reappeared_realtor
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

## Step 7: Scheduled Sync Trigger

Run the full pipeline once:

```bash
realtor-agent --sync-now
```

Run the full pipeline now, then automatically repeat every 3 hours:

```bash
realtor-agent --scheduled-sync
```

The scheduled sync runs:

```text
BCFSA API
  -> Raw Snapshot Store
  -> Validation
  -> Normalization
  -> Realtors Table
  -> Change Detection
```

Each sync run is logged in:

```text
source_runs
```

The run log stores:

```text
trigger
status
started_at
finished_at
raw snapshots stored
valid / invalid records
normalized records
saved realtors
change events created
```

Optional settings:

```bash
realtor-agent --scheduled-sync --sync-interval-hours 3
realtor-agent --sync-now --max-pages 1
```

After changing sync code on a server, restart the scheduled sync process so the
running process uses the latest code.

If the dashboard count is higher than the latest source count, run a full sync:

```bash
realtor-agent --sync-now
```

A full sync normalizes only the raw pages from that sync and removes realtor rows
that no longer exist in the latest BCFSA result set from the current directory.
The row is not deleted. It is marked as:

```text
is_currently_found = 0
removed_at = timestamp
```

Removed rows are also recorded in `change_events` as:

```text
removed_realtor
```

The dashboard shows current records by default, but the `Record state` filter can
show `Current`, `Not found`, or `All`.

## Step 8: Dashboard

Start the local dashboard:

```bash
realtor-agent --dashboard
```

The dashboard opens a small web server on all network interfaces by default:

```text
0.0.0.0:8765
```

On a VPS, open it from your browser with:

```text
http://YOUR_VPS_IP:8765
```

The dashboard reads from:

```text
data/realtor_agent.db
```

Current dashboard features:

```text
search realtor
search brokerage
filter by record state
filter by licence status
filter by city
filter by licence category
rows per page
previous/next pagination
view profile
view human-readable change feed
view sync logs
export CSV
auto-refresh every 60 seconds
```

The dashboard reads the SQLite database live. When scheduled sync creates new
`change_events` or `source_runs`, the browser updates automatically without
restarting the dashboard server.

The raw audit data still stays in `change_events`. The dashboard adds readable
messages such as:

```text
John Smith changed brokerage from ABC Realty to Elite Realty.
Jane Lee was not found in the latest full BCFSA sync.
```

Dashboard search modes:

```text
Realtors   -> searches realtor name and licence number
Brokerages -> shows brokerage rows and searches brokerage name
```

The `brokerages` table is rebuilt from saved realtor records after each sync. It
stores:

```text
brokerage
public_address
public_phone
managing_broker
current_realtor_count
not_found_realtor_count
total_realtor_count
city_count
updated_at
```

Optional settings:

```bash
realtor-agent --dashboard --port 8765
realtor-agent --dashboard --host 127.0.0.1 --port 8765
```

Use `--host 127.0.0.1` only when you intentionally want local-only access.

## Automated Tests

Run the basic regression tests:

```bash
.venv/bin/python -m pytest
```

Current tests cover:

```text
saving normalized realtors
brokerage rollups
soft removal / not-found records
human-readable change descriptions
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
