import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from realtor_agent.validation import validate_raw_snapshots

NORMALIZER_VERSION = "bcfsa_realtor_normalizer_v2"


@dataclass(frozen=True)
class NormalizationSummary:
    records_checked: int
    normalized_records: int
    skipped_records: int


def normalize_raw_snapshots(
    db_path: Path,
    *,
    validate_first: bool = True,
    raw_snapshot_ids: Iterable[int] | None = None,
) -> NormalizationSummary:
    snapshot_ids = _snapshot_ids(raw_snapshot_ids)
    if validate_first:
        validate_raw_snapshots(db_path, raw_snapshot_ids=snapshot_ids)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        _setup_normalized_table(connection)
        connection.execute("DELETE FROM normalized_realtors")

        invalid_records = _load_invalid_record_keys(connection, snapshot_ids)
        where_clause, values = _snapshot_filter(snapshot_ids)
        snapshots = connection.execute(
            f"""
            SELECT id, source, fetched_at, raw_json
            FROM raw_snapshots
            {where_clause}
            ORDER BY id
            """,
            values,
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
        "name": _format_name(record.get("name")),
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


def _snapshot_ids(raw_snapshot_ids: Iterable[int] | None) -> tuple[int, ...] | None:
    if raw_snapshot_ids is None:
        return None
    return tuple(int(snapshot_id) for snapshot_id in raw_snapshot_ids)


def _snapshot_filter(snapshot_ids: tuple[int, ...] | None) -> tuple[str, tuple[int, ...]]:
    if snapshot_ids is None:
        return "", ()
    if not snapshot_ids:
        return "WHERE 1 = 0", ()
    return f"WHERE id IN ({_placeholders(snapshot_ids)})", snapshot_ids


def _error_snapshot_filter(snapshot_ids: tuple[int, ...] | None) -> tuple[str, tuple[int, ...]]:
    if snapshot_ids is None:
        return "", ()
    if not snapshot_ids:
        return "AND 1 = 0", ()
    return f"AND raw_snapshot_id IN ({_placeholders(snapshot_ids)})", snapshot_ids


def _placeholders(values: tuple[int, ...]) -> str:
    return ",".join("?" for _ in values)


def _load_invalid_record_keys(
    connection: sqlite3.Connection,
    snapshot_ids: tuple[int, ...] | None,
) -> set[tuple[int, int]]:
    where_clause, values = _error_snapshot_filter(snapshot_ids)
    rows = connection.execute(
        f"""
        SELECT raw_snapshot_id, record_index
        FROM normalization_errors
        WHERE record_index IS NOT NULL
        {where_clause}
        """,
        values,
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


def _format_name(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None

    text = _strip_leading_symbols(text)
    text = _strip_trailing_symbols(text)
    text = " ".join(text.split())
    return text or None


def _strip_leading_symbols(text: str) -> str:
    text = text.strip()
    while text and not text[0].isalnum():
        text = text[1:].lstrip()
    return text


def _strip_trailing_symbols(text: str) -> str:
    text = text.strip()
    while text:
        cleaned = text
        cleaned = _strip_trailing_marks(cleaned)
        cleaned = _strip_trailing_dash(cleaned)
        cleaned = _strip_trailing_period(cleaned)
        if cleaned == text:
            return text
        text = cleaned.strip()
    return text


def _strip_trailing_marks(text: str) -> str:
    return text.rstrip(" '\"`,;:)]}")


def _strip_trailing_dash(text: str) -> str:
    return text.rstrip(" -–—")


def _strip_trailing_period(text: str) -> str:
    if not text.endswith(".") or _has_valid_trailing_period(text):
        return text
    return text[:-1]


def _has_valid_trailing_period(text: str) -> bool:
    token = text.rsplit(" ", 1)[-1]
    if len(token) == 2 and token[0].isalpha():
        return True
    return token.lower() in {"jr.", "sr."}
