import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from realtor_agent.validation import validate_raw_snapshots

NORMALIZER_VERSION = "bcfsa_realtor_normalizer_v1"


@dataclass(frozen=True)
class NormalizationSummary:
    records_checked: int
    normalized_records: int
    skipped_records: int


def normalize_raw_snapshots(db_path: Path) -> NormalizationSummary:
    validate_raw_snapshots(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        _setup_normalized_table(connection)
        connection.execute("DELETE FROM normalized_realtors")

        invalid_records = _load_invalid_record_keys(connection)
        snapshots = connection.execute(
            """
            SELECT id, source, fetched_at, raw_json
            FROM raw_snapshots
            ORDER BY id
            """
        ).fetchall()

        records_checked = 0
        normalized_records = 0
        skipped_records = 0

        for snapshot in snapshots:
            raw_json = json.loads(snapshot["raw_json"])
            for index, record in enumerate(raw_json.get("hits", [])):
                records_checked += 1
                if (snapshot["id"], index) in invalid_records:
                    skipped_records += 1
                    continue

                normalized = normalize_bcfsa_record(record, snapshot)
                _save_normalized_record(connection, snapshot, index, record, normalized)
                normalized_records += 1

        return NormalizationSummary(
            records_checked=records_checked,
            normalized_records=normalized_records,
            skipped_records=skipped_records,
        )


def normalize_bcfsa_record(record: dict[str, Any], snapshot: sqlite3.Row) -> dict[str, Any]:
    services = record.get("services") or []
    return {
        "source": snapshot["source"],
        "source_fetched_at": snapshot["fetched_at"],
        "license_number": _text(record.get("licence_number")),
        "name": _text(record.get("name")),
        "brokerage": _text(record.get("business_name")),
        "status": _text(record.get("status_flag")) or "Licensed",
        "city": _text(record.get("location")),
        "address": _text(record.get("address")),
        "license_level": _text(record.get("subtype")),
        "license_category": ", ".join(str(service).strip() for service in services if service),
        "source_record_id": _text(record.get("objectID")),
    }


def _setup_normalized_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS normalized_realtors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_snapshot_id INTEGER NOT NULL,
            record_index INTEGER NOT NULL,
            source TEXT NOT NULL,
            normalizer_version TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            license_number TEXT NOT NULL,
            name TEXT NOT NULL,
            brokerage TEXT NOT NULL,
            status TEXT,
            city TEXT,
            address TEXT,
            license_level TEXT,
            license_category TEXT,
            source_record_id TEXT,
            raw_record TEXT NOT NULL,
            normalized_at TEXT NOT NULL,
            FOREIGN KEY (raw_snapshot_id) REFERENCES raw_snapshots(id)
        )
        """
    )


def _load_invalid_record_keys(connection: sqlite3.Connection) -> set[tuple[int, int]]:
    rows = connection.execute(
        """
        SELECT raw_snapshot_id, record_index
        FROM normalization_errors
        WHERE record_index IS NOT NULL
        """
    ).fetchall()
    return {(row["raw_snapshot_id"], row["record_index"]) for row in rows}


def _save_normalized_record(
    connection: sqlite3.Connection,
    snapshot: sqlite3.Row,
    record_index: int,
    raw_record: dict[str, Any],
    normalized: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT INTO normalized_realtors (
            raw_snapshot_id,
            record_index,
            source,
            normalizer_version,
            source_fetched_at,
            license_number,
            name,
            brokerage,
            status,
            city,
            address,
            license_level,
            license_category,
            source_record_id,
            raw_record,
            normalized_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot["id"],
            record_index,
            normalized["source"],
            NORMALIZER_VERSION,
            normalized["source_fetched_at"],
            normalized["license_number"],
            normalized["name"],
            normalized["brokerage"],
            normalized["status"],
            normalized["city"],
            normalized["address"],
            normalized["license_level"],
            normalized["license_category"],
            normalized["source_record_id"],
            json.dumps(raw_record, sort_keys=True, separators=(",", ":")),
            datetime.now(UTC).isoformat(),
        ),
    )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
