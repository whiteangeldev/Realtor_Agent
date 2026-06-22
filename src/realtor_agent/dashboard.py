import csv
import errno
import io
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


DEFAULT_DASHBOARD_PORT = 8765


def run_dashboard(db_path: Path, host: str = "127.0.0.1", port: int = DEFAULT_DASHBOARD_PORT) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    server = _make_server(host=host, port=port, db_path=db_path)
    url = f"http://{host}:{server.server_port}"
    print(f"Dashboard running at {url}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.", flush=True)
    finally:
        server.server_close()


def _make_server(host: str, port: int, db_path: Path) -> ThreadingHTTPServer:
    for candidate_port in range(port, port + 20):
        try:
            return _DashboardServer((host, candidate_port), _DashboardHandler, db_path)
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
    raise OSError(f"No open port found from {port} to {port + 19}.")


class _DashboardServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_class: type, db_path: Path) -> None:
        super().__init__(server_address, handler_class)
        self.db_path = db_path


class _DashboardHandler(BaseHTTPRequestHandler):
    server: _DashboardServer

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/") or "/"
        params = parse_qs(parsed_url.query)

        try:
            if path == "/":
                self._send_text(HTML, "text/html; charset=utf-8")
            elif path == "/styles.css":
                self._send_text(CSS, "text/css; charset=utf-8")
            elif path == "/app.js":
                self._send_text(JS, "application/javascript; charset=utf-8")
            elif path == "/api/summary":
                self._send_json(_get_summary(self.server.db_path))
            elif path == "/api/realtors":
                self._send_json(_search_realtors(self.server.db_path, params))
            elif path == "/api/realtor":
                self._send_json(_get_realtor(self.server.db_path, params))
            elif path == "/api/brokerages":
                self._send_json(_search_brokerages(self.server.db_path, params))
            elif path == "/api/brokerage":
                self._send_json(_get_brokerage(self.server.db_path, params))
            elif path == "/api/changes":
                self._send_json(_get_changes(self.server.db_path, params))
            elif path == "/api/sync-logs":
                self._send_json(_get_sync_logs(self.server.db_path, params))
            elif path == "/export/realtors.csv":
                self._send_csv(_export_realtors_csv(self.server.db_path, params), "realtors.csv")
            elif path == "/export/brokerages.csv":
                self._send_csv(_export_brokerages_csv(self.server.db_path, params), "brokerages.csv")
            else:
                self._send_json({"error": "Not found"}, status=404)
        except (sqlite3.Error, ValueError) as error:
            self._send_json({"error": str(error)}, status=500)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_json(self, payload: dict | list, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _send_text(self, text: str, content_type: str, status: int = 200) -> None:
        self._send_bytes(text.encode("utf-8"), content_type, status)

    def _send_csv(self, csv_text: str, filename: str) -> None:
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        self._send_bytes(csv_text.encode("utf-8"), "text/csv; charset=utf-8", headers=headers)

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _get_summary(db_path: Path) -> dict:
    with _connect(db_path) as connection:
        realtor_count = connection.execute("SELECT COUNT(*) FROM realtors").fetchone()[0]
        brokerage_count = connection.execute(
            """
            SELECT COUNT(DISTINCT brokerage)
            FROM realtors
            WHERE brokerage IS NOT NULL AND brokerage != ''
            """
        ).fetchone()[0]
        change_count = connection.execute("SELECT COUNT(*) FROM change_events").fetchone()[0]
        snapshot_count = connection.execute("SELECT COUNT(*) FROM raw_snapshots").fetchone()[0]
        error_count = connection.execute("SELECT COUNT(*) FROM normalization_errors").fetchone()[0]
        latest_snapshot = connection.execute(
            "SELECT MAX(fetched_at) FROM raw_snapshots"
        ).fetchone()[0]
        latest_update = connection.execute("SELECT MAX(updated_at) FROM realtors").fetchone()[0]

    return {
        "realtors": realtor_count,
        "brokerages": brokerage_count,
        "changes": change_count,
        "raw_snapshots": snapshot_count,
        "normalization_errors": error_count,
        "latest_snapshot": latest_snapshot,
        "latest_update": latest_update,
    }


def _search_realtors(db_path: Path, params: dict[str, list[str]]) -> dict:
    search = _param(params, "search")
    page, per_page = _pagination(params)
    offset = (page - 1) * per_page

    where_clause, values = _realtor_where(search)
    sql = f"""
        SELECT
            license_number,
            name,
            brokerage,
            status,
            city,
            updated_at
        FROM realtors
        {where_clause}
        ORDER BY name COLLATE NOCASE
        LIMIT ? OFFSET ?
    """
    count_sql = f"SELECT COUNT(*) FROM realtors {where_clause}"

    with _connect(db_path) as connection:
        total = connection.execute(count_sql, values).fetchone()[0]
        rows = connection.execute(sql, [*values, per_page, offset]).fetchall()

    return {
        "rows": [_row_to_dict(row) for row in rows],
        "search": search,
        "mode": "realtor",
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": _total_pages(total, per_page),
    }


def _get_realtor(db_path: Path, params: dict[str, list[str]]) -> dict:
    license_number = _param(params, "license_number")
    if not license_number:
        raise ValueError("license_number is required")

    with _connect(db_path) as connection:
        realtor = connection.execute(
            """
            SELECT *
            FROM realtors
            WHERE license_number = ?
            """,
            (license_number,),
        ).fetchone()
        changes = _changes_for_license(connection, license_number, limit=25)

    return {
        "realtor": _row_to_dict(realtor) if realtor else None,
        "changes": [_row_to_dict(row) for row in changes],
    }


def _search_brokerages(db_path: Path, params: dict[str, list[str]]) -> dict:
    search = _param(params, "search")
    page, per_page = _pagination(params)
    offset = (page - 1) * per_page
    where_clause, values = _brokerage_where(search)

    sql = f"""
        SELECT
            brokerage,
            COUNT(*) AS realtor_count,
            COUNT(DISTINCT city) AS city_count,
            MAX(updated_at) AS updated_at
        FROM realtors
        {where_clause}
        GROUP BY brokerage
        ORDER BY brokerage COLLATE NOCASE
        LIMIT ? OFFSET ?
    """
    count_sql = f"""
        SELECT COUNT(*)
        FROM (
            SELECT brokerage
            FROM realtors
            {where_clause}
            GROUP BY brokerage
        )
    """

    with _connect(db_path) as connection:
        total = connection.execute(count_sql, values).fetchone()[0]
        rows = connection.execute(sql, [*values, per_page, offset]).fetchall()

    return {
        "rows": [_row_to_dict(row) for row in rows],
        "search": search,
        "mode": "brokerage",
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": _total_pages(total, per_page),
    }


def _get_brokerage(db_path: Path, params: dict[str, list[str]]) -> dict:
    brokerage = _param(params, "brokerage")
    if not brokerage:
        raise ValueError("brokerage is required")

    with _connect(db_path) as connection:
        summary = connection.execute(
            """
            SELECT
                brokerage,
                COUNT(*) AS realtor_count,
                COUNT(DISTINCT city) AS city_count,
                MAX(updated_at) AS updated_at
            FROM realtors
            WHERE brokerage = ?
            GROUP BY brokerage
            """,
            (brokerage,),
        ).fetchone()
        realtors = connection.execute(
            """
            SELECT
                license_number,
                name,
                status,
                city
            FROM realtors
            WHERE brokerage = ?
            ORDER BY name COLLATE NOCASE
            LIMIT 25
            """,
            (brokerage,),
        ).fetchall()

    return {
        "brokerage": _row_to_dict(summary) if summary else None,
        "realtors": [_row_to_dict(row) for row in realtors],
    }


def _get_changes(db_path: Path, params: dict[str, list[str]]) -> dict:
    license_number = _param(params, "license_number")
    brokerage = _param(params, "brokerage")
    limit = _limit(params, default=50, maximum=200)

    with _connect(db_path) as connection:
        if license_number:
            rows = _changes_for_license(connection, license_number, limit)
        elif brokerage:
            rows = connection.execute(
                """
                SELECT change_events.*
                FROM change_events
                JOIN realtors ON realtors.license_number = change_events.license_number
                WHERE realtors.brokerage = ?
                ORDER BY change_events.detected_at DESC, change_events.id DESC
                LIMIT ?
                """,
                (brokerage, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT *
                FROM change_events
                ORDER BY detected_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    return {"rows": [_row_to_dict(row) for row in rows]}


def _get_sync_logs(db_path: Path, params: dict[str, list[str]]) -> dict:
    limit = _limit(params, default=20, maximum=100)

    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                source,
                adapter_version,
                endpoint,
                query_params,
                raw_json,
                response_hash,
                fetch_status,
                fetched_at
            FROM raw_snapshots
            ORDER BY fetched_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return {"rows": [_sync_log_row(row) for row in rows]}


def _export_realtors_csv(db_path: Path, params: dict[str, list[str]]) -> str:
    search = _param(params, "search")
    where_clause, values = _realtor_where(search)

    sql = f"""
        SELECT
            license_number,
            name,
            brokerage,
            status,
            city,
            address,
            license_level,
            license_category,
            source,
            source_record_id,
            source_fetched_at,
            normalizer_version,
            first_seen_at,
            last_seen_at,
            updated_at
        FROM realtors
        {where_clause}
        ORDER BY name COLLATE NOCASE
    """

    output = io.StringIO()
    writer = csv.writer(output)
    columns = [
        "license_number",
        "name",
        "brokerage",
        "status",
        "city",
        "address",
        "license_level",
        "license_category",
        "source",
        "source_record_id",
        "source_fetched_at",
        "normalizer_version",
        "first_seen_at",
        "last_seen_at",
        "updated_at",
    ]
    writer.writerow(columns)

    with _connect(db_path) as connection:
        rows = connection.execute(sql, values).fetchall()
        for row in rows:
            writer.writerow([row[column] or "" for column in columns])

    return output.getvalue()


def _export_brokerages_csv(db_path: Path, params: dict[str, list[str]]) -> str:
    search = _param(params, "search")
    where_clause, values = _brokerage_where(search)

    sql = f"""
        SELECT
            brokerage,
            COUNT(*) AS realtor_count,
            COUNT(DISTINCT city) AS city_count,
            MAX(updated_at) AS updated_at
        FROM realtors
        {where_clause}
        GROUP BY brokerage
        ORDER BY brokerage COLLATE NOCASE
    """

    output = io.StringIO()
    writer = csv.writer(output)
    columns = ["brokerage", "realtor_count", "city_count", "updated_at"]
    writer.writerow(columns)

    with _connect(db_path) as connection:
        rows = connection.execute(sql, values).fetchall()
        for row in rows:
            writer.writerow([row[column] or "" for column in columns])

    return output.getvalue()


def _changes_for_license(
    connection: sqlite3.Connection,
    license_number: str,
    limit: int,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT *
        FROM change_events
        WHERE license_number = ?
        ORDER BY detected_at DESC, id DESC
        LIMIT ?
        """,
        (license_number, limit),
    ).fetchall()


def _realtor_where(search: str) -> tuple[str, list[str]]:
    if not search:
        return "", []

    like = f"%{search}%"
    return "WHERE name LIKE ? OR license_number LIKE ?", [like, like]


def _brokerage_where(search: str) -> tuple[str, list[str]]:
    base = "WHERE brokerage IS NOT NULL AND brokerage != ''"
    if not search:
        return base, []
    return f"{base} AND brokerage LIKE ?", [f"%{search}%"]


def _sync_log_row(row: sqlite3.Row) -> dict:
    raw_json = _load_json(row["raw_json"])
    query_params = _load_json(row["query_params"])
    if not isinstance(query_params, dict):
        query_params = {}
    hits = raw_json.get("hits") if isinstance(raw_json, dict) else None

    return {
        "id": row["id"],
        "source": row["source"],
        "adapter_version": row["adapter_version"],
        "fetch_status": row["fetch_status"],
        "fetched_at": row["fetched_at"],
        "endpoint": row["endpoint"],
        "page": query_params.get("page"),
        "hits_per_page": query_params.get("hitsPerPage"),
        "hit_count": len(hits) if isinstance(hits, list) else None,
        "response_hash": row["response_hash"],
    }


def _load_json(value: str) -> dict | list:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, (dict, list)) else {}


def _param(params: dict[str, list[str]], name: str, default: str = "") -> str:
    return params.get(name, [default])[0].strip()


def _limit(params: dict[str, list[str]], default: int, maximum: int) -> int:
    raw_value = _param(params, "limit", str(default))
    try:
        value = int(raw_value)
    except ValueError:
        value = default
    return max(1, min(value, maximum))


def _pagination(params: dict[str, list[str]]) -> tuple[int, int]:
    page = _limit_with_name(params, "page", default=1, maximum=100_000)
    per_page = _limit_with_name(params, "per_page", default=50, maximum=200)
    return page, per_page


def _limit_with_name(params: dict[str, list[str]], name: str, default: int, maximum: int) -> int:
    raw_value = _param(params, name, str(default))
    try:
        value = int(raw_value)
    except ValueError:
        value = default
    return max(1, min(value, maximum))


def _total_pages(total: int, per_page: int) -> int:
    if total == 0:
        return 1
    return (total + per_page - 1) // per_page


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Realtor Dashboard</title>
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <header class="topbar">
    <div class="brand-block">
      <span class="eyebrow">Data Pipeline Dashboard</span>
      <h1>BCFSA Realtor Intelligence</h1>
      <p id="lastUpdated">Loading database status...</p>
    </div>
    <div class="top-actions">
      <a class="export-button" id="exportLink" href="/export/realtors.csv">Export Realtors CSV</a>
    </div>
  </header>

  <main>
    <section class="metrics" aria-label="Database summary">
      <div class="metric">
        <span>Realtors</span>
        <strong id="metricRealtors">-</strong>
      </div>
      <div class="metric">
        <span>Brokerages</span>
        <strong id="metricBrokerages">-</strong>
      </div>
      <div class="metric">
        <span>Changes</span>
        <strong id="metricChanges">-</strong>
      </div>
      <div class="metric">
        <span>Raw Snapshots</span>
        <strong id="metricSnapshots">-</strong>
      </div>
    </section>

    <section class="workspace">
      <section class="search-area" aria-label="Directory">
        <div class="section-head directory-head">
          <div>
            <h2 id="resultTitle">Realtors</h2>
            <p id="resultSubtitle">Individual licence records</p>
          </div>
          <div class="segments" aria-label="Directory mode">
            <label>
              <input type="radio" name="mode" value="realtor" checked>
              <span>Realtors</span>
            </label>
            <label>
              <input type="radio" name="mode" value="brokerage">
              <span>Brokerages</span>
            </label>
          </div>
        </div>

        <form class="searchbar" id="searchForm">
          <div class="search-field">
            <label for="searchInput">Search</label>
            <input
              id="searchInput"
              name="search"
              autocomplete="off"
              placeholder="Name or licence number"
            >
          </div>
          <button type="submit">Search</button>
        </form>

        <div class="pager" aria-label="Result pagination">
          <span id="pageInfo">0 rows</span>
          <div class="page-actions">
            <label class="page-size">
              <span>Rows per page</span>
              <select id="perPageSelect">
                <option value="25">25</option>
                <option value="50" selected>50</option>
                <option value="100">100</option>
                <option value="200">200</option>
              </select>
            </label>
            <button type="button" id="prevPage">Previous</button>
            <button type="button" id="nextPage">Next</button>
          </div>
        </div>

        <div class="table-wrap">
          <table>
            <thead>
              <tr id="resultHeader"></tr>
            </thead>
            <tbody id="resultRows"></tbody>
          </table>
        </div>
      </section>

      <aside class="profile-area" aria-label="Profile">
        <div class="section-head">
          <h2 id="profileTitle">Profile</h2>
        </div>
        <div id="profileBody" class="empty-state">No profile selected</div>
      </aside>
    </section>

    <section class="lower-grid">
      <section aria-label="Recent changes">
        <div class="section-head">
          <h2>Recent Changes</h2>
        </div>
        <div class="table-wrap compact">
          <table>
            <thead>
              <tr>
                <th>Licence</th>
                <th>Event</th>
                <th>Field</th>
                <th>Old</th>
                <th>New</th>
                <th>Detected</th>
              </tr>
            </thead>
            <tbody id="changeRows"></tbody>
          </table>
        </div>
      </section>

      <section aria-label="Sync logs">
        <div class="section-head">
          <h2>Sync Logs</h2>
        </div>
        <div class="table-wrap compact">
          <table>
            <thead>
              <tr>
                <th>Fetched</th>
                <th>Source</th>
                <th>Adapter</th>
                <th>Page</th>
                <th>Hits</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody id="syncRows"></tbody>
          </table>
        </div>
      </section>
    </section>
  </main>

  <script src="/app.js"></script>
</body>
</html>
"""


CSS = """
:root {
  color-scheme: light;
  --bg: #f4f6f8;
  --surface: #ffffff;
  --surface-alt: #f8fafb;
  --ink: #202326;
  --muted: #66717d;
  --line: #d8dee4;
  --line-strong: #c7d0d9;
  --accent: #256f68;
  --accent-dark: #1f5b55;
  --accent-soft: #e7f1ef;
  --warm: #9a5f24;
  --danger: #a13f3f;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 76px;
  padding: 16px 24px;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}

.brand-block {
  min-width: 0;
}

.eyebrow {
  display: block;
  margin-bottom: 3px;
  color: var(--accent-dark);
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
}

.top-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

h1,
h2,
p {
  margin: 0;
}

h1 {
  font-size: 21px;
  font-weight: 720;
  letter-spacing: 0;
}

h2 {
  font-size: 15px;
  font-weight: 720;
  letter-spacing: 0;
}

.topbar p {
  margin-top: 4px;
  color: var(--muted);
  font-size: 13px;
}

main {
  max-width: 1680px;
  margin: 0 auto;
  padding: 18px 24px 28px;
}

.metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.metric {
  min-height: 76px;
  padding: 13px 14px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 6px;
}

.metric span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  font-weight: 650;
  text-transform: uppercase;
}

.metric strong {
  display: block;
  margin-top: 7px;
  color: var(--ink);
  font-size: 25px;
  line-height: 1;
}

.workspace {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 380px;
  gap: 16px;
  align-items: start;
}

.search-area,
.profile-area,
.lower-grid > section {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 6px;
  overflow: hidden;
  box-shadow: 0 1px 2px rgba(21, 30, 39, 0.04);
}

.searchbar {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) auto;
  gap: 10px;
  align-items: end;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}

.search-field {
  display: grid;
  gap: 5px;
}

.search-field label {
  color: var(--muted);
  font-size: 12px;
  font-weight: 760;
}

input,
select,
button,
.export-button {
  min-height: 38px;
  border-radius: 6px;
  font: inherit;
}

input {
  width: 100%;
  border: 1px solid var(--line);
  padding: 0 12px;
  color: var(--ink);
  background: var(--surface);
}

select {
  border: 1px solid var(--line);
  padding: 0 28px 0 10px;
  color: var(--ink);
  background: var(--surface);
}

input:focus {
  outline: 2px solid rgba(47, 118, 109, 0.22);
  border-color: var(--accent);
}

select:focus {
  outline: 2px solid rgba(47, 118, 109, 0.22);
  border-color: var(--accent);
}

button,
.export-button {
  border: 1px solid var(--accent);
  background: var(--accent);
  color: #ffffff;
  padding: 0 14px;
  font-weight: 720;
  cursor: pointer;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  white-space: nowrap;
}

button:hover,
.export-button:hover {
  background: var(--accent-dark);
  border-color: var(--accent-dark);
}

button:disabled {
  border-color: var(--line);
  background: #eef1eb;
  color: var(--muted);
  cursor: not-allowed;
}

.segments {
  display: inline-grid;
  grid-template-columns: repeat(2, minmax(118px, 1fr));
  border: 1px solid var(--line);
  border-radius: 6px;
  overflow: hidden;
  background: var(--surface);
}

.segments label {
  min-width: 0;
}

.segments input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.segments span {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 34px;
  padding: 0 12px;
  color: var(--muted);
  font-size: 13px;
  font-weight: 700;
  border-left: 1px solid var(--line);
  cursor: pointer;
}

.segments label:first-child span {
  border-left: 0;
}

.segments input:checked + span {
  background: var(--accent-soft);
  color: var(--accent-dark);
}

.pager {
  min-height: 50px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--surface-alt);
}

.page-size,
.page-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.page-size span,
#pageInfo {
  color: var(--muted);
  font-size: 13px;
  font-weight: 700;
}

.table-wrap {
  overflow: auto;
  max-height: 610px;
}

.table-wrap.compact {
  max-height: 310px;
}

table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}

th,
td {
  padding: 9px 12px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
  font-size: 13px;
  overflow-wrap: anywhere;
}

th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--surface-alt);
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
}

tbody tr {
  cursor: pointer;
  transition: background-color 120ms ease;
}

tbody tr:hover {
  background: #f3f7f6;
}

tbody tr.selected {
  background: var(--accent-soft);
}

.section-head {
  min-height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--surface-alt);
}

.section-head p {
  margin-top: 3px;
  color: var(--muted);
  font-size: 12px;
}

.directory-head {
  align-items: center;
}

.profile-area {
  position: sticky;
  top: 18px;
  min-height: 320px;
}

.profile-body {
  padding: 14px;
}

.profile-title {
  margin-bottom: 12px;
}

.profile-title strong {
  display: block;
  font-size: 19px;
  line-height: 1.2;
}

.profile-title span {
  display: block;
  margin-top: 4px;
  color: var(--muted);
  font-size: 13px;
}

.field-list {
  display: grid;
  gap: 9px;
}

.field {
  display: grid;
  grid-template-columns: 112px minmax(0, 1fr);
  gap: 8px;
  font-size: 13px;
}

.field span:first-child {
  color: var(--muted);
  font-weight: 700;
}

.mini-list {
  display: grid;
  gap: 8px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.mini-list h3 {
  margin: 0;
  font-size: 13px;
  letter-spacing: 0;
}

.mini-row {
  display: grid;
  gap: 2px;
  padding: 8px 0;
  border-top: 1px solid var(--line);
  font-size: 13px;
}

.mini-row strong {
  font-size: 13px;
}

.mini-row span {
  color: var(--muted);
}

.badge {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  max-width: 100%;
  padding: 2px 8px;
  border-radius: 999px;
  background: #f4eadf;
  color: var(--warm);
  font-size: 12px;
  font-weight: 760;
}

.empty-state {
  padding: 28px 14px;
  color: var(--muted);
  font-size: 13px;
}

.lower-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
  margin-top: 16px;
}

.muted {
  color: var(--muted);
}

.danger {
  color: var(--danger);
}

@media (max-width: 980px) {
  .metrics,
  .workspace,
  .lower-grid {
    grid-template-columns: 1fr;
  }

  .profile-area {
    position: static;
  }
}

@media (max-width: 680px) {
  .topbar {
    align-items: flex-start;
    flex-direction: column;
    padding: 14px;
  }

  .top-actions,
  .export-button {
    width: 100%;
  }

  main {
    padding: 14px;
  }

  .searchbar {
    grid-template-columns: 1fr;
  }

  .segments {
    width: 100%;
  }

  .segments label {
    min-width: 0;
  }

  .pager {
    align-items: stretch;
    flex-direction: column;
  }

  .page-actions {
    align-items: stretch;
    flex-direction: column;
    justify-content: space-between;
  }

  .page-size {
    justify-content: space-between;
  }
}
"""


JS = """
const TABLE_HEADERS = {
  realtor: ["Name", "Licence", "Brokerage", "Status", "City"],
  brokerage: ["Brokerage", "Realtors", "Cities", "Last Updated"],
};

const PLACEHOLDERS = {
  realtor: "Name or licence number",
  brokerage: "Brokerage name",
};

const MODE_TITLES = {
  realtor: "Realtors",
  brokerage: "Brokerages",
};

const MODE_SUBTITLES = {
  realtor: "Individual licence records",
  brokerage: "Grouped brokerage records",
};

const state = {
  search: "",
  mode: "realtor",
  page: 1,
  perPage: 50,
  totalPages: 1,
  selectedLicense: null,
  selectedBrokerage: null,
};

const nodes = {
  searchForm: document.getElementById("searchForm"),
  searchInput: document.getElementById("searchInput"),
  resultTitle: document.getElementById("resultTitle"),
  resultSubtitle: document.getElementById("resultSubtitle"),
  resultHeader: document.getElementById("resultHeader"),
  resultRows: document.getElementById("resultRows"),
  perPageSelect: document.getElementById("perPageSelect"),
  prevPage: document.getElementById("prevPage"),
  nextPage: document.getElementById("nextPage"),
  pageInfo: document.getElementById("pageInfo"),
  profileTitle: document.getElementById("profileTitle"),
  profileBody: document.getElementById("profileBody"),
  changeRows: document.getElementById("changeRows"),
  syncRows: document.getElementById("syncRows"),
  exportLink: document.getElementById("exportLink"),
  lastUpdated: document.getElementById("lastUpdated"),
  metricRealtors: document.getElementById("metricRealtors"),
  metricBrokerages: document.getElementById("metricBrokerages"),
  metricChanges: document.getElementById("metricChanges"),
  metricSnapshots: document.getElementById("metricSnapshots"),
};

nodes.searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  state.search = nodes.searchInput.value.trim();
  state.mode = currentMode();
  state.page = 1;
  clearSelection();
  updateModeUI();
  updateExportLink();
  loadResults();
});

document.querySelectorAll('input[name="mode"]').forEach((input) => {
  input.addEventListener("change", () => {
    state.mode = input.value;
    state.page = 1;
    clearSelection();
    updateModeUI();
    updateExportLink();
    loadResults();
  });
});

nodes.perPageSelect.addEventListener("change", () => {
  state.perPage = Number(nodes.perPageSelect.value);
  state.page = 1;
  clearSelection();
  loadResults();
});

nodes.prevPage.addEventListener("click", () => setPage(state.page - 1));
nodes.nextPage.addEventListener("click", () => setPage(state.page + 1));

function currentMode() {
  const checkedMode = document.querySelector('input[name="mode"]:checked');
  return checkedMode ? checkedMode.value : "realtor";
}

function api(path, params = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") {
      url.searchParams.set(key, value);
    }
  });
  return fetch(url).then((response) => {
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status}`);
    }
    return response.json();
  });
}

function clearSelection() {
  state.selectedLicense = null;
  state.selectedBrokerage = null;
  nodes.profileTitle.textContent = "Profile";
  nodes.profileBody.className = "empty-state";
  nodes.profileBody.textContent = "No profile selected";
}

function updateModeUI() {
  nodes.searchInput.placeholder = PLACEHOLDERS[state.mode] || PLACEHOLDERS.realtor;
  nodes.resultTitle.textContent = MODE_TITLES[state.mode] || MODE_TITLES.realtor;
  nodes.resultSubtitle.textContent = MODE_SUBTITLES[state.mode] || MODE_SUBTITLES.realtor;
  renderHeaders();
}

function updateExportLink() {
  const params = new URLSearchParams();
  if (state.search) {
    params.set("search", state.search);
  }
  if (state.mode === "brokerage") {
    nodes.exportLink.href = `/export/brokerages.csv?${params.toString()}`;
    nodes.exportLink.textContent = "Export Brokerages CSV";
    return;
  }
  nodes.exportLink.href = `/export/realtors.csv?${params.toString()}`;
  nodes.exportLink.textContent = "Export Realtors CSV";
}

function setPage(nextPage) {
  if (nextPage < 1 || nextPage > state.totalPages) {
    return;
  }
  state.page = nextPage;
  clearSelection();
  loadResults();
}

function renderHeaders() {
  nodes.resultHeader.replaceChildren();
  TABLE_HEADERS[state.mode].forEach((label) => {
    const header = document.createElement("th");
    header.textContent = label;
    nodes.resultHeader.appendChild(header);
  });
}

function renderPager(payload) {
  state.page = payload.page;
  state.perPage = payload.per_page;
  state.totalPages = payload.total_pages;
  const start = payload.total === 0 ? 0 : (payload.page - 1) * payload.per_page + 1;
  const end = Math.min(payload.page * payload.per_page, payload.total);
  nodes.pageInfo.textContent = `${start.toLocaleString()}-${end.toLocaleString()} of ${payload.total.toLocaleString()} | Page ${payload.page} of ${payload.total_pages}`;
  nodes.prevPage.disabled = payload.page <= 1;
  nodes.nextPage.disabled = payload.page >= payload.total_pages;
}

function columnCount() {
  return TABLE_HEADERS[state.mode].length;
}

function formatDate(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function text(value) {
  return value === null || value === undefined || value === "" ? "-" : value;
}

function setCell(row, value, className = "") {
  const cell = document.createElement("td");
  cell.textContent = text(value);
  if (className) {
    cell.className = className;
  }
  row.appendChild(cell);
}

function emptyRow(target, message, colspan) {
  target.replaceChildren();
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = colspan;
  cell.className = "muted";
  cell.textContent = message;
  row.appendChild(cell);
  target.appendChild(row);
}

function loadSummary() {
  return api("/api/summary").then((summary) => {
    nodes.metricRealtors.textContent = summary.realtors.toLocaleString();
    nodes.metricBrokerages.textContent = summary.brokerages.toLocaleString();
    nodes.metricChanges.textContent = summary.changes.toLocaleString();
    nodes.metricSnapshots.textContent = summary.raw_snapshots.toLocaleString();
    nodes.lastUpdated.textContent = `Latest sync: ${formatDate(summary.latest_snapshot)} | Errors: ${summary.normalization_errors}`;
  });
}

function loadResults() {
  renderHeaders();
  if (state.mode === "brokerage") {
    return loadBrokerages();
  }
  return loadRealtors();
}

function loadRealtors() {
  emptyRow(nodes.resultRows, "Loading...", columnCount());
  return api("/api/realtors", {
    search: state.search,
    mode: state.mode,
    page: state.page,
    per_page: state.perPage,
  }).then((payload) => {
    renderPager(payload);
    nodes.resultRows.replaceChildren();
    if (!payload.rows.length) {
      emptyRow(nodes.resultRows, "No matching records", columnCount());
      clearSelection();
      loadChanges();
      return;
    }

    payload.rows.forEach((realtor) => {
      const row = document.createElement("tr");
      row.dataset.license = realtor.license_number;
      row.addEventListener("click", () => selectRealtor(realtor.license_number));
      setCell(row, realtor.name);
      setCell(row, realtor.license_number);
      setCell(row, realtor.brokerage);
      setCell(row, realtor.status);
      setCell(row, realtor.city);
      nodes.resultRows.appendChild(row);
    });

    if (!state.selectedLicense) {
      selectRealtor(payload.rows[0].license_number);
    }
  });
}

function loadBrokerages() {
  emptyRow(nodes.resultRows, "Loading...", columnCount());
  return api("/api/brokerages", {
    search: state.search,
    page: state.page,
    per_page: state.perPage,
  }).then((payload) => {
    renderPager(payload);
    nodes.resultRows.replaceChildren();
    if (!payload.rows.length) {
      emptyRow(nodes.resultRows, "No matching brokerages", columnCount());
      clearSelection();
      loadChanges();
      return;
    }

    payload.rows.forEach((brokerage) => {
      const row = document.createElement("tr");
      row.dataset.brokerage = brokerage.brokerage;
      row.addEventListener("click", () => selectBrokerage(brokerage.brokerage));
      setCell(row, brokerage.brokerage);
      setCell(row, brokerage.realtor_count.toLocaleString());
      setCell(row, brokerage.city_count.toLocaleString());
      setCell(row, formatDate(brokerage.updated_at));
      nodes.resultRows.appendChild(row);
    });

    if (!state.selectedBrokerage) {
      selectBrokerage(payload.rows[0].brokerage);
    }
  });
}

function selectRealtor(licenseNumber) {
  state.selectedLicense = licenseNumber;
  state.selectedBrokerage = null;
  nodes.profileTitle.textContent = "Realtor Profile";
  document.querySelectorAll("#resultRows tr").forEach((row) => {
    row.classList.toggle("selected", row.dataset.license === licenseNumber);
  });
  loadProfile(licenseNumber);
  loadChanges({ license_number: licenseNumber });
}

function selectBrokerage(brokerage) {
  state.selectedBrokerage = brokerage;
  state.selectedLicense = null;
  nodes.profileTitle.textContent = "Brokerage Profile";
  document.querySelectorAll("#resultRows tr").forEach((row) => {
    row.classList.toggle("selected", row.dataset.brokerage === brokerage);
  });
  loadBrokerageProfile(brokerage);
  loadChanges({ brokerage });
}

function loadProfile(licenseNumber) {
  nodes.profileBody.className = "empty-state";
  nodes.profileBody.textContent = "Loading...";
  return api("/api/realtor", { license_number: licenseNumber }).then((payload) => {
    renderProfile(payload.realtor);
  });
}

function loadBrokerageProfile(brokerage) {
  nodes.profileBody.className = "empty-state";
  nodes.profileBody.textContent = "Loading...";
  return api("/api/brokerage", { brokerage }).then((payload) => {
    renderBrokerageProfile(payload);
  });
}

function renderProfile(realtor) {
  if (!realtor) {
    nodes.profileBody.className = "empty-state";
    nodes.profileBody.textContent = "Profile not found";
    return;
  }

  nodes.profileBody.className = "profile-body";
  nodes.profileBody.replaceChildren();

  const title = document.createElement("div");
  title.className = "profile-title";
  const name = document.createElement("strong");
  name.textContent = realtor.name;
  const license = document.createElement("span");
  license.textContent = realtor.license_number;
  title.append(name, license);

  const fields = document.createElement("div");
  fields.className = "field-list";
  [
    ["Brokerage", realtor.brokerage],
    ["Status", realtor.status],
    ["City", realtor.city],
    ["Address", realtor.address],
    ["Level", realtor.license_level],
    ["Category", realtor.license_category],
    ["Source", realtor.source],
    ["Normalizer", realtor.normalizer_version],
    ["First Seen", formatDate(realtor.first_seen_at)],
    ["Last Seen", formatDate(realtor.last_seen_at)],
  ].forEach(([label, value]) => {
    const field = document.createElement("div");
    field.className = "field";
    const labelNode = document.createElement("span");
    labelNode.textContent = label;
    const valueNode = document.createElement("span");
    valueNode.textContent = text(value);
    field.append(labelNode, valueNode);
    fields.appendChild(field);
  });

  nodes.profileBody.append(title, fields);
}

function renderBrokerageProfile(payload) {
  const brokerage = payload.brokerage;
  if (!brokerage) {
    nodes.profileBody.className = "empty-state";
    nodes.profileBody.textContent = "Brokerage not found";
    return;
  }

  nodes.profileBody.className = "profile-body";
  nodes.profileBody.replaceChildren();

  const title = document.createElement("div");
  title.className = "profile-title";
  const name = document.createElement("strong");
  name.textContent = brokerage.brokerage;
  const subtitle = document.createElement("span");
  subtitle.textContent = `${brokerage.realtor_count.toLocaleString()} realtors`;
  title.append(name, subtitle);

  const fields = document.createElement("div");
  fields.className = "field-list";
  [
    ["Realtors", brokerage.realtor_count.toLocaleString()],
    ["Cities", brokerage.city_count.toLocaleString()],
    ["Last Updated", formatDate(brokerage.updated_at)],
  ].forEach(([label, value]) => {
    const field = document.createElement("div");
    field.className = "field";
    const labelNode = document.createElement("span");
    labelNode.textContent = label;
    const valueNode = document.createElement("span");
    valueNode.textContent = text(value);
    field.append(labelNode, valueNode);
    fields.appendChild(field);
  });

  const list = document.createElement("div");
  list.className = "mini-list";
  const listTitle = document.createElement("h3");
  listTitle.textContent = "Realtors";
  list.appendChild(listTitle);

  payload.realtors.forEach((realtor) => {
    const row = document.createElement("div");
    row.className = "mini-row";
    const rowName = document.createElement("strong");
    rowName.textContent = realtor.name;
    const rowMeta = document.createElement("span");
    rowMeta.textContent = `${realtor.license_number} | ${text(realtor.status)} | ${text(realtor.city)}`;
    row.append(rowName, rowMeta);
    list.appendChild(row);
  });

  nodes.profileBody.append(title, fields, list);
}

function loadChanges(filters = {}) {
  emptyRow(nodes.changeRows, "Loading...", 6);
  return api("/api/changes", { limit: 50, ...filters }).then((payload) => {
    nodes.changeRows.replaceChildren();
    if (!payload.rows.length) {
      emptyRow(nodes.changeRows, "No changes recorded", 6);
      return;
    }
    payload.rows.forEach((change) => {
      const row = document.createElement("tr");
      setCell(row, change.license_number);
      setCell(row, change.event_type);
      setCell(row, change.field_name);
      setCell(row, change.old_value);
      setCell(row, change.new_value);
      setCell(row, formatDate(change.detected_at));
      nodes.changeRows.appendChild(row);
    });
  });
}

function loadSyncLogs() {
  emptyRow(nodes.syncRows, "Loading...", 6);
  return api("/api/sync-logs", { limit: 20 }).then((payload) => {
    nodes.syncRows.replaceChildren();
    if (!payload.rows.length) {
      emptyRow(nodes.syncRows, "No sync logs", 6);
      return;
    }
    payload.rows.forEach((log) => {
      const row = document.createElement("tr");
      setCell(row, formatDate(log.fetched_at));
      setCell(row, log.source);
      setCell(row, log.adapter_version);
      setCell(row, log.page);
      setCell(row, log.hit_count);
      setCell(row, log.fetch_status);
      nodes.syncRows.appendChild(row);
    });
  });
}

function boot() {
  updateModeUI();
  updateExportLink();
  Promise.all([loadSummary(), loadResults(), loadSyncLogs()]).catch((error) => {
    nodes.lastUpdated.textContent = error.message;
    nodes.lastUpdated.className = "danger";
  });
}

boot();
"""
