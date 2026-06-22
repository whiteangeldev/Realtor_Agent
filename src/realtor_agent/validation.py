import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VALIDATOR_VERSION = "raw_validator_v1"


@dataclass(frozen=True)
class ValidationSummary:
    snapshots_checked: int
    records_checked: int
    valid_records: int
    invalid_records: int


def validate_raw_snapshots(db_path: Path) -> ValidationSummary:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        _setup_errors_table(connection)
        if _table_exists(connection, "raw_snapshots"):
            _add_column_if_missing(
                connection,
                table_name="raw_snapshots",
                column_name="adapter_version",
                column_sql="TEXT NOT NULL DEFAULT 'unknown'",
            )
        connection.execute("DELETE FROM normalization_errors")

        if not _table_exists(connection, "raw_snapshots"):
            return ValidationSummary(
                snapshots_checked=0,
                records_checked=0,
                valid_records=0,
                invalid_records=0,
            )

        snapshots = connection.execute(
            """
            SELECT id, source, fetched_at, raw_json
            FROM raw_snapshots
            ORDER BY id
            """
        ).fetchall()

        records_checked = 0
        valid_records = 0
        invalid_records = 0

        for snapshot in snapshots:
            snapshot_errors = _validate_snapshot(snapshot)
            if snapshot_errors:
                _save_error(
                    connection=connection,
                    snapshot=snapshot,
                    record_index=None,
            raw_record={"raw_json": snapshot["raw_json"]},
                    errors=snapshot_errors,
                )
                invalid_records += 1
                continue

            raw_json = json.loads(snapshot["raw_json"])
            for index, record in enumerate(raw_json["hits"]):
                records_checked += 1
                errors = _validate_realtor_record(record, snapshot)
                if errors:
                    invalid_records += 1
                    _save_error(
                        connection=connection,
                        snapshot=snapshot,
                        record_index=index,
                        raw_record=record,
                        errors=errors,
                    )
                else:
                    valid_records += 1

        return ValidationSummary(
            snapshots_checked=len(snapshots),
            records_checked=records_checked,
            valid_records=valid_records,
            invalid_records=invalid_records,
        )


def _setup_errors_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS normalization_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_snapshot_id INTEGER NOT NULL,
            record_index INTEGER,
            source TEXT NOT NULL,
            validator_version TEXT NOT NULL,
            licence_number TEXT,
            error_message TEXT NOT NULL,
            raw_record TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (raw_snapshot_id) REFERENCES raw_snapshots(id)
        )
        """
    )
    _add_column_if_missing(
        connection,
        table_name="normalization_errors",
        column_name="validator_version",
        column_sql="TEXT NOT NULL DEFAULT 'unknown'",
    )


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


def _validate_snapshot(snapshot: sqlite3.Row) -> list[str]:
    errors = []
    if not snapshot["source"]:
        errors.append("source is missing")
    if not snapshot["fetched_at"]:
        errors.append("source timestamp is missing")

    try:
        raw_json = json.loads(snapshot["raw_json"])
    except json.JSONDecodeError:
        return [*errors, "raw_json is not valid JSON"]

    if not isinstance(raw_json, dict):
        errors.append("raw_json is not an object")
    elif not isinstance(raw_json.get("hits"), list):
        errors.append("raw_json.hits is missing or not a list")
    return errors


def _validate_realtor_record(record: Any, snapshot: sqlite3.Row) -> list[str]:
    if not isinstance(record, dict):
        return ["record is not an object"]

    errors = []
    if not _has_text(record.get("licence_number")):
        errors.append("licence number is missing")
    if not _has_text(record.get("name")):
        errors.append("name is missing")
    if not _has_text(record.get("business_name")):
        errors.append("brokerage name is missing")
    if not snapshot["fetched_at"]:
        errors.append("source timestamp is missing")

    status = record.get("status_flag")
    if status is not None and not isinstance(status, str):
        errors.append("licence status has invalid type")

    for field in ("licence_number", "name", "business_name", "location", "address"):
        value = record.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"{field} has invalid type")

    services = record.get("services")
    if services is not None and not isinstance(services, list):
        errors.append("services has invalid type")

    return errors


def _save_error(
    *,
    connection: sqlite3.Connection,
    snapshot: sqlite3.Row,
    record_index: int | None,
    raw_record: Any,
    errors: list[str],
) -> None:
    licence_number = raw_record.get("licence_number") if isinstance(raw_record, dict) else None
    connection.execute(
        """
        INSERT INTO normalization_errors (
            raw_snapshot_id,
            record_index,
            source,
            validator_version,
            licence_number,
            error_message,
            raw_record,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot["id"],
            record_index,
            snapshot["source"] or "unknown",
            VALIDATOR_VERSION,
            licence_number,
            "; ".join(errors),
            json.dumps(raw_record, sort_keys=True, separators=(",", ":")),
            datetime.now(UTC).isoformat(),
        ),
    )


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
