import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from realtor_agent.source_adapters.base import RawSourcePage


class RawSnapshotStore:
    """Stores raw source responses before validation or normalization."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def setup(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    adapter_version TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    query_params TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    response_hash TEXT NOT NULL,
                    fetch_status TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                )
                """
            )
            _add_column_if_missing(
                connection,
                table_name="raw_snapshots",
                column_name="adapter_version",
                column_sql="TEXT NOT NULL DEFAULT 'unknown'",
            )
            _add_column_if_missing(
                connection,
                table_name="raw_snapshots",
                column_name="source_run_id",
                column_sql="INTEGER",
            )

    def save(self, page: RawSourcePage, *, source_run_id: int | None = None) -> int:
        self.setup()
        raw_json = _to_json(page.raw_json)
        fetched_at = datetime.now(UTC).isoformat()

        with sqlite3.connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO raw_snapshots (
                    source_run_id,
                    source,
                    adapter_version,
                    endpoint,
                    query_params,
                    raw_json,
                    response_hash,
                    fetch_status,
                    fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_run_id,
                    page.source,
                    page.adapter_version,
                    page.endpoint,
                    _to_json(page.query_params),
                    raw_json,
                    hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
                    "success",
                    fetched_at,
                ),
            )
            return int(cursor.lastrowid)


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


def _to_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
