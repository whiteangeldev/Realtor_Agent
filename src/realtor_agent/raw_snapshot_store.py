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
                    endpoint TEXT NOT NULL,
                    query_params TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    response_hash TEXT NOT NULL,
                    fetch_status TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                )
                """
            )

    def save(self, page: RawSourcePage) -> int:
        self.setup()
        raw_json = _to_json(page.raw_json)
        fetched_at = datetime.now(UTC).isoformat()

        with sqlite3.connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO raw_snapshots (
                    source,
                    endpoint,
                    query_params,
                    raw_json,
                    response_hash,
                    fetch_status,
                    fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page.source,
                    page.endpoint,
                    _to_json(page.query_params),
                    raw_json,
                    hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
                    "success",
                    fetched_at,
                ),
            )
            return int(cursor.lastrowid)


def _to_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
