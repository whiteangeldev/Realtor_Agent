import sqlite3
from pathlib import Path

from realtor_agent.sync import _evaluate_sync_safety, _setup_source_runs_table


def test_full_sync_safety_guard_skips_removals_when_count_drops_too_far(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "realtors.db"
    _insert_successful_full_sync(db_path, normalized_records=30_000)

    safety = _evaluate_sync_safety(
        db_path=db_path,
        run_id=2,
        is_full_sync=True,
        normalized_records=15_000,
        min_full_sync_record_ratio=0.85,
    )

    assert safety.detect_removals is False
    assert safety.removal_detection_skipped is True
    assert safety.warning is not None
    assert "15,000" in safety.warning
    assert "30,000" in safety.warning


def test_full_sync_safety_guard_allows_removals_when_count_is_close(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "realtors.db"
    _insert_successful_full_sync(db_path, normalized_records=30_000)

    safety = _evaluate_sync_safety(
        db_path=db_path,
        run_id=2,
        is_full_sync=True,
        normalized_records=29_000,
        min_full_sync_record_ratio=0.85,
    )

    assert safety.detect_removals is True
    assert safety.removal_detection_skipped is False
    assert safety.warning is None


def test_limited_sync_never_detects_removals(tmp_path: Path) -> None:
    db_path = tmp_path / "realtors.db"
    _insert_successful_full_sync(db_path, normalized_records=30_000)

    safety = _evaluate_sync_safety(
        db_path=db_path,
        run_id=2,
        is_full_sync=False,
        normalized_records=100,
        min_full_sync_record_ratio=0.85,
    )

    assert safety.detect_removals is False
    assert safety.removal_detection_skipped is True
    assert safety.warning is None


def _insert_successful_full_sync(db_path: Path, *, normalized_records: int) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        _setup_source_runs_table(connection)
        connection.execute(
            """
            INSERT INTO source_runs (
                source,
                trigger,
                status,
                started_at,
                finished_at,
                normalized_records,
                is_full_sync
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "BCFSA",
                "scheduled",
                "success",
                "2026-06-22T00:00:00+00:00",
                "2026-06-22T00:10:00+00:00",
                normalized_records,
                1,
            ),
        )
