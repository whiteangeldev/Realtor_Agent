import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class RealtorSaveSummary:
    normalized_rows_checked: int
    realtor_rows_saved: int
    total_realtors: int


def save_realtors_from_normalized(db_path: Path) -> RealtorSaveSummary:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        _setup_realtors_table(connection)

        normalized_rows = connection.execute(
            """
            SELECT *
            FROM normalized_realtors
            ORDER BY id
            """
        ).fetchall()

        saved = 0
        for row in normalized_rows:
            _upsert_realtor(connection, row)
            saved += 1

        total_realtors = connection.execute("SELECT COUNT(*) FROM realtors").fetchone()[0]
        return RealtorSaveSummary(
            normalized_rows_checked=len(normalized_rows),
            realtor_rows_saved=saved,
            total_realtors=total_realtors,
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


def _upsert_realtor(connection: sqlite3.Connection, row: sqlite3.Row) -> None:
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
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            updated_at = excluded.updated_at
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
        ),
    )
