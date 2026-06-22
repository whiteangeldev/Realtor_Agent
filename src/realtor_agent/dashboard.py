import csv
import errno
import io
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


DEFAULT_DASHBOARD_PORT = 8765
DEFAULT_DASHBOARD_HOST = "0.0.0.0"


def run_dashboard(
    db_path: Path,
    host: str = DEFAULT_DASHBOARD_HOST,
    port: int = DEFAULT_DASHBOARD_PORT,
) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    server = _make_server(host=host, port=port, db_path=db_path)
    url = f"http://{host}:{server.server_port}"
    print(f"Dashboard running at {url}", flush=True)
    if host == DEFAULT_DASHBOARD_HOST:
        print(f"Use http://<your-vps-ip>:{server.server_port} from your browser.", flush=True)
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
            elif path == "/api/filter-options":
                self._send_json(_get_filter_options(self.server.db_path))
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


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _add_column_if_missing(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    column_name: str,
    column_sql: str,
) -> None:
    columns = {
        row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def _ensure_dashboard_schema(connection: sqlite3.Connection) -> None:
    if _table_exists(connection, "realtors"):
        _add_column_if_missing(
            connection,
            table_name="realtors",
            column_name="is_currently_found",
            column_sql="INTEGER NOT NULL DEFAULT 1",
        )
        _add_column_if_missing(
            connection,
            table_name="realtors",
            column_name="removed_at",
            column_sql="TEXT",
        )
        _add_column_if_missing(
            connection,
            table_name="realtors",
            column_name="last_seen_run_id",
            column_sql="INTEGER",
        )
    _setup_brokerages_table(connection)
    brokerage_count = connection.execute("SELECT COUNT(*) FROM brokerages").fetchone()[0]
    if brokerage_count == 0 and _table_exists(connection, "realtors"):
        _rebuild_brokerages(connection)


def _setup_brokerages_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS brokerages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brokerage TEXT NOT NULL UNIQUE,
            public_address TEXT,
            public_phone TEXT,
            managing_broker TEXT,
            current_realtor_count INTEGER NOT NULL DEFAULT 0,
            not_found_realtor_count INTEGER NOT NULL DEFAULT 0,
            total_realtor_count INTEGER NOT NULL DEFAULT 0,
            city_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )


def _rebuild_brokerages(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM brokerages")
    connection.execute(
        """
        INSERT INTO brokerages (
            brokerage,
            public_address,
            public_phone,
            managing_broker,
            current_realtor_count,
            not_found_realtor_count,
            total_realtor_count,
            city_count,
            updated_at
        )
        SELECT
            brokerage,
            MIN(CASE WHEN address IS NOT NULL AND address != '' THEN address END),
            NULL,
            NULL,
            SUM(CASE WHEN is_currently_found = 1 THEN 1 ELSE 0 END),
            SUM(CASE WHEN is_currently_found = 0 THEN 1 ELSE 0 END),
            COUNT(*),
            COUNT(DISTINCT CASE WHEN city IS NOT NULL AND city != '' THEN city END),
            MAX(updated_at)
        FROM realtors
        WHERE brokerage IS NOT NULL AND brokerage != ''
        GROUP BY brokerage
        """
    )


def _distinct_values(connection: sqlite3.Connection, column_name: str) -> list[str]:
    rows = connection.execute(
        f"""
        SELECT DISTINCT {column_name}
        FROM realtors
        WHERE is_currently_found = 1
          AND {column_name} IS NOT NULL
          AND {column_name} != ''
        ORDER BY {column_name} COLLATE NOCASE
        """
    ).fetchall()
    return [row[0] for row in rows]


def _get_summary(db_path: Path) -> dict:
    with _connect(db_path) as connection:
        _ensure_dashboard_schema(connection)
        realtor_count = connection.execute(
            "SELECT COUNT(*) FROM realtors WHERE is_currently_found = 1"
        ).fetchone()[0]
        brokerage_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM brokerages
            WHERE current_realtor_count > 0
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


def _get_filter_options(db_path: Path) -> dict:
    with _connect(db_path) as connection:
        _ensure_dashboard_schema(connection)
        statuses = _distinct_values(connection, "status")
        cities = _distinct_values(connection, "city")
        categories = _distinct_values(connection, "license_category")

    return {
        "statuses": statuses,
        "cities": cities,
        "categories": categories,
    }


def _search_realtors(db_path: Path, params: dict[str, list[str]]) -> dict:
    search = _param(params, "search")
    page, per_page = _pagination(params)
    offset = (page - 1) * per_page

    where_clause, values = _realtor_where(params)
    sql = f"""
        SELECT
            license_number,
            name,
            brokerage,
            status,
            city,
            license_category,
            is_currently_found,
            removed_at,
            updated_at
        FROM realtors
        {where_clause}
        ORDER BY name COLLATE NOCASE
        LIMIT ? OFFSET ?
    """
    count_sql = f"SELECT COUNT(*) FROM realtors {where_clause}"

    with _connect(db_path) as connection:
        _ensure_dashboard_schema(connection)
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
        _ensure_dashboard_schema(connection)
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
        "changes": [_change_row_to_dict(row) for row in changes],
    }


def _search_brokerages(db_path: Path, params: dict[str, list[str]]) -> dict:
    search = _param(params, "search")
    page, per_page = _pagination(params)
    offset = (page - 1) * per_page
    where_clause, values = _brokerage_where(params)

    sql = f"""
        SELECT
            brokerage,
            public_address,
            managing_broker,
            current_realtor_count,
            not_found_realtor_count,
            total_realtor_count,
            city_count,
            updated_at
        FROM brokerages
        {where_clause}
        ORDER BY brokerage COLLATE NOCASE
        LIMIT ? OFFSET ?
    """
    count_sql = f"SELECT COUNT(*) FROM brokerages {where_clause}"

    with _connect(db_path) as connection:
        _ensure_dashboard_schema(connection)
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
        _ensure_dashboard_schema(connection)
        summary = connection.execute(
            """
            SELECT
                brokerage,
                public_address,
                public_phone,
                managing_broker,
                current_realtor_count,
                not_found_realtor_count,
                total_realtor_count,
                city_count,
                updated_at
            FROM brokerages
            WHERE brokerage = ?
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
              AND is_currently_found = 1
            ORDER BY name COLLATE NOCASE
            LIMIT 25
            """,
            (brokerage,),
        ).fetchall()
        changes = connection.execute(
            """
            SELECT
                change_events.*,
                realtors.name AS realtor_name,
                realtors.brokerage AS current_brokerage
            FROM change_events
            JOIN realtors ON realtors.license_number = change_events.license_number
            WHERE realtors.brokerage = ?
            ORDER BY change_events.detected_at DESC, change_events.id DESC
            LIMIT 10
            """,
            (brokerage,),
        ).fetchall()

    return {
        "brokerage": _row_to_dict(summary) if summary else None,
        "realtors": [_row_to_dict(row) for row in realtors],
        "changes": [_change_row_to_dict(row) for row in changes],
    }


def _get_changes(db_path: Path, params: dict[str, list[str]]) -> dict:
    license_number = _param(params, "license_number")
    brokerage = _param(params, "brokerage")
    limit = _limit(params, default=50, maximum=200)

    with _connect(db_path) as connection:
        _ensure_dashboard_schema(connection)
        if license_number:
            rows = _changes_for_license(connection, license_number, limit)
        elif brokerage:
            rows = connection.execute(
                """
                SELECT
                    change_events.*,
                    realtors.name AS realtor_name,
                    realtors.brokerage AS current_brokerage
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
                SELECT
                    change_events.*,
                    realtors.name AS realtor_name,
                    realtors.brokerage AS current_brokerage
                FROM change_events
                LEFT JOIN realtors ON realtors.license_number = change_events.license_number
                ORDER BY detected_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    return {"rows": [_change_row_to_dict(row) for row in rows]}


def _get_sync_logs(db_path: Path, params: dict[str, list[str]]) -> dict:
    limit = _limit(params, default=20, maximum=100)

    with _connect(db_path) as connection:
        if not _table_exists(connection, "source_runs"):
            return {"rows": []}
        _add_column_if_missing(
            connection,
            table_name="source_runs",
            column_name="removed_realtors",
            column_sql="INTEGER NOT NULL DEFAULT 0",
        )

        rows = connection.execute(
            """
            SELECT
                id,
                source,
                trigger,
                status,
                started_at,
                finished_at,
                raw_snapshots_stored,
                valid_records,
                invalid_records,
                normalized_records,
                realtor_rows_saved,
                change_events_created,
                removed_realtors,
                error_message
            FROM source_runs
            ORDER BY started_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return {"rows": [_row_to_dict(row) for row in rows]}


def _export_realtors_csv(db_path: Path, params: dict[str, list[str]]) -> str:
    where_clause, values = _realtor_where(params)

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
            is_currently_found,
            removed_at,
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
        "is_currently_found",
        "removed_at",
        "updated_at",
    ]
    writer.writerow(columns)

    with _connect(db_path) as connection:
        _ensure_dashboard_schema(connection)
        rows = connection.execute(sql, values).fetchall()
        for row in rows:
            writer.writerow([row[column] or "" for column in columns])

    return output.getvalue()


def _export_brokerages_csv(db_path: Path, params: dict[str, list[str]]) -> str:
    where_clause, values = _brokerage_where(params)

    sql = f"""
        SELECT
            brokerage,
            public_address,
            public_phone,
            managing_broker,
            current_realtor_count,
            not_found_realtor_count,
            total_realtor_count,
            city_count,
            updated_at
        FROM brokerages
        {where_clause}
        ORDER BY brokerage COLLATE NOCASE
    """

    output = io.StringIO()
    writer = csv.writer(output)
    columns = [
        "brokerage",
        "public_address",
        "public_phone",
        "managing_broker",
        "current_realtor_count",
        "not_found_realtor_count",
        "total_realtor_count",
        "city_count",
        "updated_at",
    ]
    writer.writerow(columns)

    with _connect(db_path) as connection:
        _ensure_dashboard_schema(connection)
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
        SELECT
            change_events.*,
            realtors.name AS realtor_name,
            realtors.brokerage AS current_brokerage
        FROM change_events
        LEFT JOIN realtors ON realtors.license_number = change_events.license_number
        WHERE change_events.license_number = ?
        ORDER BY detected_at DESC, id DESC
        LIMIT ?
        """,
        (license_number, limit),
    ).fetchall()


def _change_row_to_dict(row: sqlite3.Row) -> dict:
    change = _row_to_dict(row)
    change["event_label"] = _change_event_label(change["event_type"])
    change["description"] = _change_description(change)
    return change


def _change_event_label(event_type: str) -> str:
    return {
        "new_realtor": "New realtor",
        "removed_realtor": "Not found",
        "reappeared_realtor": "Found again",
        "brokerage_changed": "Brokerage changed",
        "status_changed": "Status changed",
        "location_changed": "Location changed",
        "profile_changed": "Profile changed",
    }.get(event_type, event_type.replace("_", " ").title())


def _change_description(change: dict) -> str:
    name = change.get("realtor_name") or change.get("old_value") or change["license_number"]
    field_name = _field_label(change.get("field_name"))
    old_value = _display_value(change.get("old_value"))
    new_value = _display_value(change.get("new_value"))

    if change["event_type"] == "new_realtor":
        return f"{name} was added to the directory."
    if change["event_type"] == "removed_realtor":
        return f"{name} was not found in the latest full BCFSA sync."
    if change["event_type"] == "reappeared_realtor":
        return f"{name} appeared again in the latest BCFSA sync."
    if change["event_type"] == "brokerage_changed":
        return f"{name} changed brokerage from {old_value} to {new_value}."
    if change["event_type"] == "status_changed":
        return f"{name} changed licence status from {old_value} to {new_value}."
    if change["event_type"] == "location_changed":
        return f"{name} changed {field_name} from {old_value} to {new_value}."
    if change["event_type"] == "profile_changed":
        return f"{name} changed {field_name} from {old_value} to {new_value}."
    return f"{name}: {_change_event_label(change['event_type'])}."


def _field_label(field_name: str | None) -> str:
    return {
        "name": "name",
        "brokerage": "brokerage",
        "status": "licence status",
        "city": "city",
        "address": "address",
        "license_level": "licence level",
        "license_category": "licence category",
    }.get(field_name or "profile", "profile")


def _display_value(value: str | None) -> str:
    if value is None or value == "":
        return "empty"
    return str(value)


def _realtor_where(params: dict[str, list[str]]) -> tuple[str, list[str]]:
    search = _param(params, "search")
    found = _param(params, "found", "current")
    status = _param(params, "status")
    city = _param(params, "city")
    category = _param(params, "category")

    clauses = []
    values = []

    if found == "not_found":
        clauses.append("is_currently_found = 0")
    elif found != "all":
        clauses.append("is_currently_found = 1")

    if not search:
        pass
    else:
        like = f"%{search}%"
        clauses.append("(name LIKE ? OR license_number LIKE ?)")
        values.extend([like, like])

    if status:
        clauses.append("status = ?")
        values.append(status)
    if city:
        clauses.append("city = ?")
        values.append(city)
    if category:
        clauses.append("license_category = ?")
        values.append(category)

    if not clauses:
        return "", []
    return f"WHERE {' AND '.join(clauses)}", values


def _brokerage_where(params: dict[str, list[str]]) -> tuple[str, list[str]]:
    search = _param(params, "search")
    found = _param(params, "found", "current")

    clauses = ["brokerage IS NOT NULL", "brokerage != ''"]
    values = []

    if found == "not_found":
        clauses.append("current_realtor_count = 0")
        clauses.append("not_found_realtor_count > 0")
    elif found != "all":
        clauses.append("current_realtor_count > 0")

    if search:
        clauses.append("brokerage LIKE ?")
        values.append(f"%{search}%")

    return f"WHERE {' AND '.join(clauses)}", values


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
      <button type="button" class="ghost-button" id="refreshButton">Refresh</button>
      <span class="refresh-status" id="refreshStatus">Auto-refresh on</span>
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

        <div class="filterbar" aria-label="Directory filters">
          <label>
            <span>Record state</span>
            <select id="foundFilter">
              <option value="current">Current</option>
              <option value="not_found">Not found</option>
              <option value="all">All</option>
            </select>
          </label>
          <label class="realtor-filter">
            <span>Status</span>
            <select id="statusFilter">
              <option value="">All statuses</option>
            </select>
          </label>
          <label class="realtor-filter">
            <span>City</span>
            <select id="cityFilter">
              <option value="">All cities</option>
            </select>
          </label>
          <label class="realtor-filter">
            <span>Category</span>
            <select id="categoryFilter">
              <option value="">All categories</option>
            </select>
          </label>
          <button type="button" class="ghost-button" id="clearFilters">Clear</button>
        </div>

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
                <th>Change</th>
                <th>Licence</th>
                <th>Details</th>
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
                <th>Started</th>
                <th>Trigger</th>
                <th>Status</th>
                <th>Pages</th>
                <th>Valid / Invalid</th>
                <th>Removed</th>
                <th>Changes</th>
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

.refresh-status {
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
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

.filterbar {
  display: grid;
  grid-template-columns: repeat(4, minmax(150px, 1fr)) auto;
  gap: 10px;
  align-items: end;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--surface-alt);
}

.filterbar label {
  display: grid;
  gap: 5px;
}

.filterbar span {
  color: var(--muted);
  font-size: 12px;
  font-weight: 760;
}

.hidden {
  display: none !important;
}

input,
select,
button,
.ghost-button,
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

.ghost-button {
  border: 1px solid var(--line-strong);
  background: var(--surface);
  color: var(--ink);
  padding: 0 12px;
  font-weight: 720;
  cursor: pointer;
}

button:hover,
.export-button:hover {
  background: var(--accent-dark);
  border-color: var(--accent-dark);
}

.ghost-button:hover {
  background: var(--surface-alt);
  border-color: var(--line-strong);
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
  .export-button,
  .ghost-button {
    width: 100%;
  }

  main {
    padding: 14px;
  }

  .searchbar {
    grid-template-columns: 1fr;
  }

  .filterbar {
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
const AUTO_REFRESH_MS = 60000;

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
  found: "current",
  status: "",
  city: "",
  category: "",
  page: 1,
  perPage: 50,
  totalPages: 1,
  selectedLicense: null,
  selectedBrokerage: null,
  isRefreshing: false,
};

const nodes = {
  searchForm: document.getElementById("searchForm"),
  searchInput: document.getElementById("searchInput"),
  foundFilter: document.getElementById("foundFilter"),
  statusFilter: document.getElementById("statusFilter"),
  cityFilter: document.getElementById("cityFilter"),
  categoryFilter: document.getElementById("categoryFilter"),
  clearFilters: document.getElementById("clearFilters"),
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
  refreshButton: document.getElementById("refreshButton"),
  refreshStatus: document.getElementById("refreshStatus"),
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
nodes.refreshButton.addEventListener("click", () => refreshDashboard({ silent: false }));

[nodes.foundFilter, nodes.statusFilter, nodes.cityFilter, nodes.categoryFilter].forEach((filter) => {
  filter.addEventListener("change", () => {
    readFilters();
    state.page = 1;
    clearSelection();
    updateExportLink();
    loadResults();
  });
});

nodes.clearFilters.addEventListener("click", () => {
  nodes.foundFilter.value = "current";
  nodes.statusFilter.value = "";
  nodes.cityFilter.value = "";
  nodes.categoryFilter.value = "";
  readFilters();
  state.page = 1;
  clearSelection();
  updateExportLink();
  loadResults();
});

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
  document.querySelectorAll(".realtor-filter").forEach((node) => {
    node.classList.toggle("hidden", state.mode === "brokerage");
  });
  renderHeaders();
}

function updateExportLink() {
  const params = new URLSearchParams();
  if (state.search) {
    params.set("search", state.search);
  }
  params.set("found", state.found);
  if (state.mode === "brokerage") {
    nodes.exportLink.href = `/export/brokerages.csv?${params.toString()}`;
    nodes.exportLink.textContent = "Export Brokerages CSV";
    return;
  }
  if (state.status) {
    params.set("status", state.status);
  }
  if (state.city) {
    params.set("city", state.city);
  }
  if (state.category) {
    params.set("category", state.category);
  }
  nodes.exportLink.href = `/export/realtors.csv?${params.toString()}`;
  nodes.exportLink.textContent = "Export Realtors CSV";
}

function readFilters() {
  state.found = nodes.foundFilter.value;
  state.status = nodes.statusFilter.value;
  state.city = nodes.cityFilter.value;
  state.category = nodes.categoryFilter.value;
}

function setRefreshStatus(message) {
  nodes.refreshStatus.textContent = message;
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

function loadFilterOptions() {
  return api("/api/filter-options").then((options) => {
    setOptions(nodes.statusFilter, options.statuses, "All statuses");
    setOptions(nodes.cityFilter, options.cities, "All cities");
    setOptions(nodes.categoryFilter, options.categories, "All categories");
  });
}

function setOptions(select, values, emptyLabel) {
  const currentValue = select.value;
  select.replaceChildren();
  const emptyOption = document.createElement("option");
  emptyOption.value = "";
  emptyOption.textContent = emptyLabel;
  select.appendChild(emptyOption);

  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  });

  select.value = values.includes(currentValue) ? currentValue : "";
  readFilters();
}

function loadResults(options = {}) {
  renderHeaders();
  if (state.mode === "brokerage") {
    return loadBrokerages(options);
  }
  return loadRealtors(options);
}

function loadRealtors(options = {}) {
  if (!options.silent) {
    emptyRow(nodes.resultRows, "Loading...", columnCount());
  }
  return api("/api/realtors", {
    search: state.search,
    found: state.found,
    status: state.status,
    city: state.city,
    category: state.category,
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
      setCell(row, realtor.is_currently_found ? realtor.status : "Not found");
      setCell(row, realtor.city);
      nodes.resultRows.appendChild(row);
    });

    if (state.selectedLicense) {
      highlightSelection();
    } else {
      selectRealtor(payload.rows[0].license_number);
    }
  });
}

function loadBrokerages(options = {}) {
  if (!options.silent) {
    emptyRow(nodes.resultRows, "Loading...", columnCount());
  }
  return api("/api/brokerages", {
    search: state.search,
    found: state.found,
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
      setCell(row, brokerage.current_realtor_count.toLocaleString());
      setCell(row, brokerage.city_count.toLocaleString());
      setCell(row, formatDate(brokerage.updated_at));
      nodes.resultRows.appendChild(row);
    });

    if (state.selectedBrokerage) {
      highlightSelection();
    } else {
      selectBrokerage(payload.rows[0].brokerage);
    }
  });
}

function highlightSelection() {
  document.querySelectorAll("#resultRows tr").forEach((row) => {
    row.classList.toggle(
      "selected",
      row.dataset.license === state.selectedLicense ||
        row.dataset.brokerage === state.selectedBrokerage
    );
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
}

function selectBrokerage(brokerage) {
  state.selectedBrokerage = brokerage;
  state.selectedLicense = null;
  nodes.profileTitle.textContent = "Brokerage Profile";
  document.querySelectorAll("#resultRows tr").forEach((row) => {
    row.classList.toggle("selected", row.dataset.brokerage === brokerage);
  });
  loadBrokerageProfile(brokerage);
}

function loadProfile(licenseNumber, options = {}) {
  if (!options.silent) {
    nodes.profileBody.className = "empty-state";
    nodes.profileBody.textContent = "Loading...";
  }
  return api("/api/realtor", { license_number: licenseNumber }).then((payload) => {
    renderProfile(payload.realtor, payload.changes || []);
  });
}

function loadBrokerageProfile(brokerage, options = {}) {
  if (!options.silent) {
    nodes.profileBody.className = "empty-state";
    nodes.profileBody.textContent = "Loading...";
  }
  return api("/api/brokerage", { brokerage }).then((payload) => {
    renderBrokerageProfile(payload);
  });
}

function renderProfile(realtor, changes = []) {
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
    ["Record State", realtor.is_currently_found ? "Current" : "Not found"],
    ["Status", realtor.status],
    ["City", realtor.city],
    ["Address", realtor.address],
    ["Level", realtor.license_level],
    ["Category", realtor.license_category],
    ["Source", realtor.source],
    ["Normalizer", realtor.normalizer_version],
    ["First Seen", formatDate(realtor.first_seen_at)],
    ["Last Seen", formatDate(realtor.last_seen_at)],
    ["Removed At", formatDate(realtor.removed_at)],
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

  nodes.profileBody.append(title, fields, miniChangeList(changes));
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
  subtitle.textContent = `${brokerage.current_realtor_count.toLocaleString()} current realtors`;
  title.append(name, subtitle);

  const fields = document.createElement("div");
  fields.className = "field-list";
  [
    ["Current", brokerage.current_realtor_count.toLocaleString()],
    ["Not Found", brokerage.not_found_realtor_count.toLocaleString()],
    ["Total Seen", brokerage.total_realtor_count.toLocaleString()],
    ["Cities", brokerage.city_count.toLocaleString()],
    ["Address", brokerage.public_address],
    ["Managing Broker", brokerage.managing_broker],
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

  nodes.profileBody.append(title, fields, list, miniChangeList(payload.changes || []));
}

function miniChangeList(changes) {
  const list = document.createElement("div");
  list.className = "mini-list";
  const listTitle = document.createElement("h3");
  listTitle.textContent = "Recent changes";
  list.appendChild(listTitle);

  if (!changes.length) {
    const empty = document.createElement("div");
    empty.className = "mini-row";
    const textNode = document.createElement("span");
    textNode.textContent = "No changes recorded";
    empty.appendChild(textNode);
    list.appendChild(empty);
    return list;
  }

  changes.slice(0, 5).forEach((change) => {
    const row = document.createElement("div");
    row.className = "mini-row";
    const label = document.createElement("strong");
    label.textContent = change.event_label;
    const description = document.createElement("span");
    description.textContent = change.description;
    row.append(label, description);
    list.appendChild(row);
  });

  return list;
}

function loadChanges(filters = {}, options = {}) {
  const columns = 4;
  if (!options.silent) {
    emptyRow(nodes.changeRows, "Loading...", columns);
  }
  return api("/api/changes", { limit: 50, ...filters }).then((payload) => {
    nodes.changeRows.replaceChildren();
    if (!payload.rows.length) {
      emptyRow(nodes.changeRows, "No changes recorded", columns);
      return;
    }
    payload.rows.forEach((change) => {
      const row = document.createElement("tr");
      setCell(row, change.event_label);
      setCell(row, change.license_number);
      setCell(row, change.description);
      setCell(row, formatDate(change.detected_at));
      nodes.changeRows.appendChild(row);
    });
  });
}

function loadSyncLogs(options = {}) {
  const columns = 7;
  if (!options.silent) {
    emptyRow(nodes.syncRows, "Loading...", columns);
  }
  return api("/api/sync-logs", { limit: 20 }).then((payload) => {
    nodes.syncRows.replaceChildren();
    if (!payload.rows.length) {
      emptyRow(nodes.syncRows, "No sync logs", columns);
      return;
    }
    payload.rows.forEach((log) => {
      const row = document.createElement("tr");
      setCell(row, formatDate(log.started_at));
      setCell(row, log.trigger);
      setCell(row, log.status);
      setCell(row, log.raw_snapshots_stored);
      setCell(row, `${log.valid_records.toLocaleString()} / ${log.invalid_records.toLocaleString()}`);
      setCell(row, log.removed_realtors);
      setCell(row, log.change_events_created);
      nodes.syncRows.appendChild(row);
    });
  });
}

function refreshCurrentDetail(options = {}) {
  if (state.selectedLicense) {
    return loadProfile(state.selectedLicense, options);
  }
  if (state.selectedBrokerage) {
    return loadBrokerageProfile(state.selectedBrokerage, options);
  }
  return Promise.resolve();
}

function refreshDashboard(options = {}) {
  if (state.isRefreshing) {
    return Promise.resolve();
  }

  state.isRefreshing = true;
  setRefreshStatus("Refreshing...");
  return Promise.all([
    loadSummary(),
    loadResults(options),
    loadChanges({}, options),
    loadSyncLogs(options),
  ])
    .then(() => refreshCurrentDetail(options))
    .then(() => {
      setRefreshStatus(`Updated ${new Date().toLocaleTimeString()}`);
    })
    .catch((error) => {
      setRefreshStatus("Refresh failed");
      nodes.lastUpdated.textContent = error.message;
      nodes.lastUpdated.className = "danger";
    })
    .finally(() => {
      state.isRefreshing = false;
    });
}

function boot() {
  updateModeUI();
  updateExportLink();
  loadFilterOptions()
    .then(() => refreshDashboard({ silent: false }))
    .catch((error) => {
      nodes.lastUpdated.textContent = error.message;
      nodes.lastUpdated.className = "danger";
    });
  window.setInterval(() => refreshDashboard({ silent: true }), AUTO_REFRESH_MS);
}

boot();
"""
