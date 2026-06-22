import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class RealtorSaveSummary:
    normalized_rows_checked: int
    realtor_rows_saved: int
    removed_realtors: int
    total_realtors: int
    change_events_created: int


def save_realtors_from_normalized(
    db_path: Path,
    *,
    detect_removals: bool = True,
    source_run_id: int | None = None,
) -> RealtorSaveSummary:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        _setup_realtors_table(connection)
        _setup_change_events_table(connection)
        _setup_brokerages_table(connection)

        latest_normalized_rows = connection.execute(
            """
            WITH latest_normalized AS (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY license_number
                        ORDER BY source_fetched_at DESC, id DESC
                    ) AS row_number
                FROM normalized_realtors
            )
            SELECT *
            FROM latest_normalized
            WHERE row_number = 1
            ORDER BY id
            """
        ).fetchall()

        saved = 0
        change_events_created = 0
        for row in latest_normalized_rows:
            change_events_created += _detect_and_save_changes(connection, row)
            _upsert_realtor(connection, row, source_run_id=source_run_id)
            saved += 1

        removed = 0
        if detect_removals and latest_normalized_rows:
            removed = _detect_and_remove_missing_realtors(connection, latest_normalized_rows)
            change_events_created += removed

        _rebuild_brokerages(connection)

        total_realtors = connection.execute(
            "SELECT COUNT(*) FROM realtors WHERE is_currently_found = 1"
        ).fetchone()[0]
        return RealtorSaveSummary(
            normalized_rows_checked=len(latest_normalized_rows),
            realtor_rows_saved=saved,
            removed_realtors=removed,
            total_realtors=total_realtors,
            change_events_created=change_events_created,
        )


def _setup_realtors_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS realtors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_number TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            brokerage TEXT NOT NULL,
            status TEXT,
            city TEXT,
            address TEXT,
            license_level TEXT,
            license_category TEXT,
            source TEXT NOT NULL,
            source_record_id TEXT,
            source_fetched_at TEXT NOT NULL,
            normalizer_version TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
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


def _setup_change_events_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS change_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_number TEXT NOT NULL,
            event_type TEXT NOT NULL,
            field_name TEXT,
            old_value TEXT,
            new_value TEXT,
            source TEXT NOT NULL,
            normalizer_version TEXT NOT NULL,
            detected_at TEXT NOT NULL
        )
        """
    )


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


def _detect_and_save_changes(connection: sqlite3.Connection, row: sqlite3.Row) -> int:
    existing = connection.execute(
        """
        SELECT *
        FROM realtors
        WHERE license_number = ?
        """,
        (row["license_number"],),
    ).fetchone()

    if existing is None:
        _save_change_event(
            connection=connection,
            row=row,
            event_type="new_realtor",
            field_name=None,
            old_value=None,
            new_value=row["license_number"],
        )
        return 1

    changes = 0
    if existing["is_currently_found"] == 0:
        _save_change_event(
            connection=connection,
            row=row,
            event_type="reappeared_realtor",
            field_name=None,
            old_value="not_found",
            new_value="found",
        )
        changes += 1

    for field_name in _TRACKED_FIELDS:
        old_value = existing[field_name]
        new_value = row[field_name]
        if _clean(old_value) == _clean(new_value):
            continue
        _save_change_event(
            connection=connection,
            row=row,
            event_type=_event_type_for_field(field_name),
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
        )
        changes += 1
    return changes


def _detect_and_remove_missing_realtors(
    connection: sqlite3.Connection,
    latest_normalized_rows: list[sqlite3.Row],
) -> int:
    current_license_numbers = {row["license_number"] for row in latest_normalized_rows}
    connection.execute("DROP TABLE IF EXISTS current_sync_licenses")
    connection.execute("CREATE TEMP TABLE current_sync_licenses (license_number TEXT PRIMARY KEY)")
    connection.executemany(
        "INSERT INTO current_sync_licenses (license_number) VALUES (?)",
        ((license_number,) for license_number in current_license_numbers),
    )
    missing_realtors = connection.execute(
        """
        SELECT realtors.*
        FROM realtors
        LEFT JOIN current_sync_licenses
          ON current_sync_licenses.license_number = realtors.license_number
        WHERE realtors.is_currently_found = 1
          AND current_sync_licenses.license_number IS NULL
        """
    ).fetchall()

    removed = 0
    now = datetime.now(UTC).isoformat()
    for realtor in missing_realtors:
        _save_removed_realtor_event(connection, realtor, detected_at=now)
        connection.execute(
            """
            UPDATE realtors
            SET
                is_currently_found = 0,
                removed_at = ?,
                updated_at = ?
            WHERE license_number = ?
            """,
            (now, now, realtor["license_number"]),
        )
        removed += 1
    return removed


_TRACKED_FIELDS = (
    "name",
    "brokerage",
    "status",
    "city",
    "address",
    "license_level",
    "license_category",
)


def _event_type_for_field(field_name: str) -> str:
    return {
        "brokerage": "brokerage_changed",
        "status": "status_changed",
        "city": "location_changed",
        "address": "location_changed",
    }.get(field_name, "profile_changed")


def _save_change_event(
    *,
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    event_type: str,
    field_name: str | None,
    old_value: str | None,
    new_value: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO change_events (
            license_number,
            event_type,
            field_name,
            old_value,
            new_value,
            source,
            normalizer_version,
            detected_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["license_number"],
            event_type,
            field_name,
            old_value,
            new_value,
            row["source"],
            row["normalizer_version"],
            datetime.now(UTC).isoformat(),
        ),
    )


def _save_removed_realtor_event(
    connection: sqlite3.Connection,
    realtor: sqlite3.Row,
    *,
    detected_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO change_events (
            license_number,
            event_type,
            field_name,
            old_value,
            new_value,
            source,
            normalizer_version,
            detected_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            realtor["license_number"],
            "removed_realtor",
            None,
            realtor["name"],
            None,
            realtor["source"],
            realtor["normalizer_version"],
            detected_at,
        ),
    )


def _clean(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _upsert_realtor(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    source_run_id: int | None,
) -> None:
    now = datetime.now(UTC).isoformat()
    connection.execute(
        """
        INSERT INTO realtors (
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
            updated_at,
            is_currently_found,
            removed_at,
            last_seen_run_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(license_number) DO UPDATE SET
            name = excluded.name,
            brokerage = excluded.brokerage,
            status = excluded.status,
            city = excluded.city,
            address = excluded.address,
            license_level = excluded.license_level,
            license_category = excluded.license_category,
            source = excluded.source,
            source_record_id = excluded.source_record_id,
            source_fetched_at = excluded.source_fetched_at,
            normalizer_version = excluded.normalizer_version,
            last_seen_at = excluded.last_seen_at,
            updated_at = excluded.updated_at,
            is_currently_found = excluded.is_currently_found,
            removed_at = excluded.removed_at,
            last_seen_run_id = excluded.last_seen_run_id
        """,
        (
            row["license_number"],
            row["name"],
            row["brokerage"],
            row["status"],
            row["city"],
            row["address"],
            row["license_level"],
            row["license_category"],
            row["source"],
            row["source_record_id"],
            row["source_fetched_at"],
            row["normalizer_version"],
            now,
            now,
            now,
            1,
            None,
            source_run_id,
        ),
    )


def _rebuild_brokerages(connection: sqlite3.Connection) -> None:
    now = datetime.now(UTC).isoformat()
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
            ?
        FROM realtors
        WHERE brokerage IS NOT NULL AND brokerage != ''
        GROUP BY brokerage
        """,
        (now,),
    )


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
