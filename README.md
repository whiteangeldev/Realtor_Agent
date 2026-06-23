# Realtor Agent

Realtor Agent syncs BCFSA realtor data into SQLite, tracks meaningful profile
changes over time, and serves a small browser dashboard for searching realtors,
brokerages, sync logs, and change history.

## Features

- Fetches realtor records from the BCFSA Algolia source.
- Stores raw snapshots, normalized rows, current realtor profiles, brokerages,
  sync runs, and change events in `data/realtor_agent.db`.
- Treats the first import as the baseline, so the initial dataset does not
  create thousands of fake `new_realtor` changes.
- Runs one-time syncs or a scheduled sync loop.
- Provides a dashboard with realtor search, brokerage search, profile details,
  CSV export, change history, and sync health warnings.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

Fill in `.env`:

```env
BCFSA_ALGOLIA_APP_ID=...
BCFSA_ALGOLIA_API_KEY=...
BCFSA_ALGOLIA_INDEX=...
BCFSA_ALGOLIA_FILTERS=...
```

## Run

Run one full sync:

```bash
realtor-agent --sync-now
```

Run sync now, then repeat every 3 hours:

```bash
realtor-agent --scheduled-sync
```

Start the dashboard:

```bash
realtor-agent --dashboard --host 0.0.0.0 --port 9421
```

Open:

```text
http://YOUR_SERVER_IP:9421
```

Useful options:

```bash
realtor-agent --scheduled-sync --sync-interval-hours 3
realtor-agent --sync-now --max-pages 1
realtor-agent --sync-now --min-full-sync-record-ratio 0.85
realtor-agent --dashboard --host 127.0.0.1 --port 9421
```

## Data Flow

```text
BCFSA API
  -> raw_snapshots
  -> normalized_realtors
  -> realtors
  -> brokerages
  -> change_events
  -> source_runs
```

`realtors` stores the current profile state. `change_events` stores changes
found after the baseline import, such as brokerage, location, status, profile,
new realtor, removed realtor, and reappeared realtor events. `source_runs`
stores sync status, counts, warnings, and errors.

The sync has a partial-result safety guard. If a full sync returns far fewer
records than the previous good full sync, it saves the fetched rows but skips
removal detection so a temporary API issue does not mark thousands of realtors
as not found.

## Production

Run both long-lived processes with systemd:

```text
deploy/realtor-agent-sync.service.example
deploy/realtor-agent-dashboard.service.example
```

Copy both examples, edit `User`, `WorkingDirectory`, `EnvironmentFile`, and the
absolute paths in `ExecStart`, then enable them:

```bash
sudo cp deploy/realtor-agent-sync.service.example /etc/systemd/system/realtor-agent-sync.service
sudo cp deploy/realtor-agent-dashboard.service.example /etc/systemd/system/realtor-agent-dashboard.service
sudo nano /etc/systemd/system/realtor-agent-sync.service
sudo nano /etc/systemd/system/realtor-agent-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now realtor-agent-sync realtor-agent-dashboard
sudo systemctl status realtor-agent-sync realtor-agent-dashboard --no-pager -l
```

Common operations:

```bash
journalctl -u realtor-agent-sync -f
journalctl -u realtor-agent-dashboard -f
sudo systemctl restart realtor-agent-sync realtor-agent-dashboard
```

## Development

Run tests:

```bash
.venv/bin/python -m pytest
```

Run pipeline stages manually when debugging:

```bash
realtor-agent --query smith --hits-per-page 2 --store-raw
realtor-agent --validate-raw
realtor-agent --normalize
realtor-agent --save-realtors
```
