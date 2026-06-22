import sqlite3
from pathlib import Path

from realtor_agent.dashboard import _get_changes
from realtor_agent.realtor_store import save_realtors_from_normalized


def test_save_realtors_creates_current_rows_and_brokerage_rollup(tmp_path: Path) -> None:
    db_path = tmp_path / "realtors.db"
    _replace_normalized_rows(
        db_path,
        [
            _normalized_row("LIC-1", "John Smith", "ABC Realty", city="Vancouver"),
            _normalized_row("LIC-2", "Jane Lee", "ABC Realty", city="Burnaby"),
        ],
    )

    summary = save_realtors_from_normalized(db_path, detect_removals=True, source_run_id=1)

    assert summary.normalized_rows_checked == 2
    assert summary.realtor_rows_saved == 2
    assert summary.removed_realtors == 0
    assert summary.total_realtors == 2
    assert summary.change_events_created == 2

    with sqlite3.connect(db_path) as connection:
        realtor_count = connection.execute(
            "SELECT COUNT(*) FROM realtors WHERE is_currently_found = 1"
        ).fetchone()[0]
        brokerage = connection.execute(
            """
            SELECT current_realtor_count, not_found_realtor_count, total_realtor_count, city_count
            FROM brokerages
            WHERE brokerage = 'ABC Realty'
            """
        ).fetchone()

    assert realtor_count == 2
    assert brokerage == (2, 0, 2, 2)


def test_missing_realtor_is_marked_not_found_not_deleted(tmp_path: Path) -> None:
    db_path = tmp_path / "realtors.db"
    _replace_normalized_rows(
        db_path,
        [
            _normalized_row("LIC-1", "John Smith", "ABC Realty"),
            _normalized_row("LIC-2", "Jane Lee", "Legacy Realty"),
        ],
    )
    save_realtors_from_normalized(db_path, detect_removals=True, source_run_id=1)

    _replace_normalized_rows(
        db_path,
        [
            _normalized_row("LIC-1", "John Smith", "ABC Realty"),
        ],
    )
    summary = save_realtors_from_normalized(db_path, detect_removals=True, source_run_id=2)

    assert summary.removed_realtors == 1
    assert summary.total_realtors == 1

    with sqlite3.connect(db_path) as connection:
        removed = connection.execute(
            """
            SELECT is_currently_found, removed_at
            FROM realtors
            WHERE license_number = 'LIC-2'
            """
        ).fetchone()
        event_type = connection.execute(
            """
            SELECT event_type
            FROM change_events
            WHERE license_number = 'LIC-2'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()[0]
        brokerage = connection.execute(
            """
            SELECT current_realtor_count, not_found_realtor_count, total_realtor_count
            FROM brokerages
            WHERE brokerage = 'Legacy Realty'
            """
        ).fetchone()

    assert removed[0] == 0
    assert removed[1]
    assert event_type == "removed_realtor"
    assert brokerage == (0, 1, 1)


def test_change_feed_has_human_readable_description(tmp_path: Path) -> None:
    db_path = tmp_path / "realtors.db"
    _replace_normalized_rows(
        db_path,
        [
            _normalized_row("LIC-1", "John Smith", "ABC Realty"),
        ],
    )
    save_realtors_from_normalized(db_path, detect_removals=True, source_run_id=1)

    _replace_normalized_rows(
        db_path,
        [
            _normalized_row("LIC-1", "John Smith", "Elite Realty"),
        ],
    )
    save_realtors_from_normalized(db_path, detect_removals=False, source_run_id=2)

    payload = _get_changes(db_path, {"license_number": ["LIC-1"]})
    latest_change = payload["rows"][0]

    assert latest_change["event_type"] == "brokerage_changed"
    assert latest_change["event_label"] == "Brokerage changed"
    assert (
        latest_change["description"]
        == "John Smith changed brokerage from ABC Realty to Elite Realty."
    )


def _replace_normalized_rows(db_path: Path, rows: list[dict[str, str | None]]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        _setup_normalized_table(connection)
        connection.execute("DELETE FROM normalized_realtors")
        connection.executemany(
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
            [
                (
                    1,
                    index,
                    row["source"],
                    row["normalizer_version"],
                    row["source_fetched_at"],
                    row["license_number"],
                    row["name"],
                    row["brokerage"],
                    row["status"],
                    row["city"],
                    row["address"],
                    row["license_level"],
                    row["license_category"],
                    row["source_record_id"],
                    "{}",
                    row["normalized_at"],
                )
                for index, row in enumerate(rows)
            ],
        )


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
            normalized_at TEXT NOT NULL
        )
        """
    )


def _normalized_row(
    license_number: str,
    name: str,
    brokerage: str,
    *,
    status: str = "Licensed",
    city: str = "Vancouver",
) -> dict[str, str | None]:
    return {
        "source": "BCFSA",
        "normalizer_version": "bcfsa_realtor_normalizer_v1",
        "source_fetched_at": "2026-06-22T00:00:00+00:00",
        "license_number": license_number,
        "name": name,
        "brokerage": brokerage,
        "status": status,
        "city": city,
        "address": "123 Main Street",
        "license_level": "Representative",
        "license_category": "trading",
        "source_record_id": license_number,
        "normalized_at": "2026-06-22T00:00:00+00:00",
    }
