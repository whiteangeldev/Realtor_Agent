import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from realtor_agent.normalization import NormalizationSummary, normalize_raw_snapshots
from realtor_agent.raw_snapshot_store import RawSnapshotStore
from realtor_agent.realtor_store import RealtorSaveSummary, save_realtors_from_normalized
from realtor_agent.source_adapters import BCFSAAlgoliaAdapter
from realtor_agent.validation import ValidationSummary, validate_raw_snapshots

DEFAULT_SYNC_INTERVAL_HOURS = 3.0
DEFAULT_MIN_FULL_SYNC_RECORD_RATIO = 0.85


@dataclass(frozen=True)
class SyncSummary:
    run_id: int
    status: str
    raw_snapshots_stored: int
    validation: ValidationSummary
    normalization: NormalizationSummary
    realtor_save: RealtorSaveSummary
    removal_detection_skipped: bool
    safety_warning: str | None
    started_at: str
    finished_at: str


@dataclass(frozen=True)
class SyncSafetyResult:
    detect_removals: bool
    removal_detection_skipped: bool
    warning: str | None


def run_sync_once(
    *,
    db_path: Path,
    query: str = "",
    hits_per_page: int = 1000,
    max_pages: int | None = None,
    trigger: str = "manual",
    min_full_sync_record_ratio: float = DEFAULT_MIN_FULL_SYNC_RECORD_RATIO,
) -> SyncSummary:
    started_at = datetime.now(UTC).isoformat()
    is_full_sync = _should_detect_removals(query=query, max_pages=max_pages)
    run_id = _start_source_run(
        db_path,
        trigger=trigger,
        started_at=started_at,
        is_full_sync=is_full_sync,
    )
    raw_snapshots_stored = 0
    raw_snapshot_ids: list[int] = []

    try:
        adapter = BCFSAAlgoliaAdapter()
        store = RawSnapshotStore(db_path)

        for page in adapter.fetch_pages(
            query=query,
            hits_per_page=hits_per_page,
            max_pages=max_pages,
        ):
            snapshot_id = store.save(page, source_run_id=run_id)
            raw_snapshot_ids.append(snapshot_id)
            raw_snapshots_stored += 1

        validation = validate_raw_snapshots(db_path, raw_snapshot_ids=raw_snapshot_ids)
        normalization = normalize_raw_snapshots(
            db_path,
            validate_first=False,
            raw_snapshot_ids=raw_snapshot_ids,
        )
        safety = _evaluate_sync_safety(
            db_path=db_path,
            run_id=run_id,
            is_full_sync=is_full_sync,
            normalized_records=normalization.normalized_records,
            min_full_sync_record_ratio=min_full_sync_record_ratio,
        )
        realtor_save = save_realtors_from_normalized(
            db_path,
            detect_removals=safety.detect_removals,
            source_run_id=run_id,
        )
        finished_at = datetime.now(UTC).isoformat()
        status = "warning" if safety.warning else "success"

        _finish_source_run(
            db_path,
            run_id=run_id,
            status=status,
            finished_at=finished_at,
            raw_snapshots_stored=raw_snapshots_stored,
            validation=validation,
            normalization=normalization,
            realtor_save=realtor_save,
            removal_detection_skipped=safety.removal_detection_skipped,
            safety_warning=safety.warning,
            error_message=None,
        )

        return SyncSummary(
            run_id=run_id,
            status=status,
            raw_snapshots_stored=raw_snapshots_stored,
            validation=validation,
            normalization=normalization,
            realtor_save=realtor_save,
            removal_detection_skipped=safety.removal_detection_skipped,
            safety_warning=safety.warning,
            started_at=started_at,
            finished_at=finished_at,
        )
    except Exception as error:
        _finish_source_run(
            db_path,
            run_id=run_id,
            status="failed",
            finished_at=datetime.now(UTC).isoformat(),
            raw_snapshots_stored=raw_snapshots_stored,
            validation=None,
            normalization=None,
            realtor_save=None,
            removal_detection_skipped=False,
            safety_warning=None,
            error_message=str(error),
        )
        raise


def run_scheduled_sync(
    *,
    db_path: Path,
    interval_hours: float = DEFAULT_SYNC_INTERVAL_HOURS,
    query: str = "",
    hits_per_page: int = 1000,
    max_pages: int | None = None,
    min_full_sync_record_ratio: float = DEFAULT_MIN_FULL_SYNC_RECORD_RATIO,
) -> None:
    interval_seconds = max(60, int(interval_hours * 60 * 60))
    print(
        f"Scheduled sync started. Interval: {interval_hours:g} hour(s).",
        flush=True,
    )
    print("Press Ctrl+C to stop.", flush=True)

    try:
        while True:
            started = datetime.now(UTC)
            print(f"\n[{started.isoformat()}] Running scheduled sync...", flush=True)
            try:
                summary = run_sync_once(
                    db_path=db_path,
                    query=query,
                    hits_per_page=hits_per_page,
                    max_pages=max_pages,
                    min_full_sync_record_ratio=min_full_sync_record_ratio,
                    trigger="scheduled",
                )
                _print_sync_summary(summary)
            except Exception as error:
                print(f"Scheduled sync failed: {error}", flush=True)

            next_run = started + timedelta(seconds=interval_seconds)
            print(f"Next sync: {next_run.isoformat()}", flush=True)
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("\nScheduled sync stopped.", flush=True)


def _print_sync_summary(summary: SyncSummary) -> None:
    print(
        "Sync complete. "
        f"Run ID: {summary.run_id}. "
        f"Status: {summary.status}. "
        f"Raw snapshots: {summary.raw_snapshots_stored}. "
        f"Valid records: {summary.validation.valid_records}. "
        f"Invalid records: {summary.validation.invalid_records}. "
        f"Normalized: {summary.normalization.normalized_records}. "
        f"Realtors saved: {summary.realtor_save.realtor_rows_saved}. "
        f"Removed: {summary.realtor_save.removed_realtors}. "
        f"Change events: {summary.realtor_save.change_events_created}.",
        flush=True,
    )
    if summary.safety_warning:
        print(f"Warning: {summary.safety_warning}", flush=True)


def _should_detect_removals(*, query: str, max_pages: int | None) -> bool:
    return query == "" and max_pages is None


def _evaluate_sync_safety(
    *,
    db_path: Path,
    run_id: int,
    is_full_sync: bool,
    normalized_records: int,
    min_full_sync_record_ratio: float,
) -> SyncSafetyResult:
    if not is_full_sync:
        return SyncSafetyResult(
            detect_removals=False,
            removal_detection_skipped=True,
            warning=None,
        )

    baseline_records = _baseline_full_sync_record_count(db_path, exclude_run_id=run_id)
    if baseline_records is None or baseline_records == 0:
        return SyncSafetyResult(
            detect_removals=True,
            removal_detection_skipped=False,
            warning=None,
        )

    minimum_expected = int(baseline_records * min_full_sync_record_ratio)
    if normalized_records >= minimum_expected:
        return SyncSafetyResult(
            detect_removals=True,
            removal_detection_skipped=False,
            warning=None,
        )

    percent = min_full_sync_record_ratio * 100
    warning = (
        "Removal detection skipped: latest full sync normalized "
        f"{normalized_records:,} records, below {percent:.0f}% of the previous full-sync "
        f"baseline ({baseline_records:,}). Existing current realtor rows were not marked "
        "as not found."
    )
    return SyncSafetyResult(
        detect_removals=False,
        removal_detection_skipped=True,
        warning=warning,
    )


def _baseline_full_sync_record_count(db_path: Path, *, exclude_run_id: int) -> int | None:
    with sqlite3.connect(db_path) as connection:
        _setup_source_runs_table(connection)
        row = connection.execute(
            """
            SELECT normalized_records
            FROM source_runs
            WHERE id != ?
              AND is_full_sync = 1
              AND status = 'success'
              AND normalized_records > 0
            ORDER BY finished_at DESC, id DESC
            LIMIT 1
            """,
            (exclude_run_id,),
        ).fetchone()
        if row:
            return int(row[0])

        if _table_exists(connection, "realtors"):
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM realtors
                WHERE is_currently_found = 1
                """
            ).fetchone()
            if row and row[0] > 0:
                return int(row[0])

    return None


def _setup_source_runs_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS source_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            trigger TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            raw_snapshots_stored INTEGER NOT NULL DEFAULT 0,
            records_checked INTEGER NOT NULL DEFAULT 0,
            valid_records INTEGER NOT NULL DEFAULT 0,
            invalid_records INTEGER NOT NULL DEFAULT 0,
            normalized_records INTEGER NOT NULL DEFAULT 0,
            realtor_rows_saved INTEGER NOT NULL DEFAULT 0,
            change_events_created INTEGER NOT NULL DEFAULT 0,
            removed_realtors INTEGER NOT NULL DEFAULT 0,
            is_full_sync INTEGER NOT NULL DEFAULT 0,
            removal_detection_skipped INTEGER NOT NULL DEFAULT 0,
            safety_warning TEXT,
            error_message TEXT
        )
        """
    )
    _add_column_if_missing(
        connection,
        table_name="source_runs",
        column_name="removed_realtors",
        column_sql="INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        connection,
        table_name="source_runs",
        column_name="is_full_sync",
        column_sql="INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        connection,
        table_name="source_runs",
        column_name="removal_detection_skipped",
        column_sql="INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        connection,
        table_name="source_runs",
        column_name="safety_warning",
        column_sql="TEXT",
    )


def _start_source_run(
    db_path: Path,
    *,
    trigger: str,
    started_at: str,
    is_full_sync: bool,
) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        _setup_source_runs_table(connection)
        cursor = connection.execute(
            """
            INSERT INTO source_runs (
                source,
                trigger,
                status,
                started_at,
                is_full_sync
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            ("BCFSA", trigger, "running", started_at, int(is_full_sync)),
        )
        return int(cursor.lastrowid)


def _finish_source_run(
    db_path: Path,
    *,
    run_id: int,
    status: str,
    finished_at: str,
    raw_snapshots_stored: int,
    validation: ValidationSummary | None,
    normalization: NormalizationSummary | None,
    realtor_save: RealtorSaveSummary | None,
    removal_detection_skipped: bool,
    safety_warning: str | None,
    error_message: str | None,
) -> None:
    with sqlite3.connect(db_path) as connection:
        _setup_source_runs_table(connection)
        connection.execute(
            """
            UPDATE source_runs
            SET
                status = ?,
                finished_at = ?,
                raw_snapshots_stored = ?,
                records_checked = ?,
                valid_records = ?,
                invalid_records = ?,
                normalized_records = ?,
                realtor_rows_saved = ?,
                change_events_created = ?,
                removed_realtors = ?,
                removal_detection_skipped = ?,
                safety_warning = ?,
                error_message = ?
            WHERE id = ?
            """,
            (
                status,
                finished_at,
                raw_snapshots_stored,
                validation.records_checked if validation else 0,
                validation.valid_records if validation else 0,
                validation.invalid_records if validation else 0,
                normalization.normalized_records if normalization else 0,
                realtor_save.realtor_rows_saved if realtor_save else 0,
                realtor_save.change_events_created if realtor_save else 0,
                realtor_save.removed_realtors if realtor_save else 0,
                int(removal_detection_skipped),
                safety_warning,
                error_message,
                run_id,
            ),
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
