import argparse
import json
from pathlib import Path

from realtor_agent.dashboard import DEFAULT_DASHBOARD_HOST, DEFAULT_DASHBOARD_PORT, run_dashboard
from realtor_agent.normalization import normalize_raw_snapshots
from realtor_agent.raw_snapshot_store import RawSnapshotStore
from realtor_agent.realtor_store import save_realtors_from_normalized
from realtor_agent.source_adapters import BCFSAAlgoliaAdapter
from realtor_agent.sync import (
    DEFAULT_MIN_FULL_SYNC_RECORD_RATIO,
    DEFAULT_SYNC_INTERVAL_HOURS,
    run_scheduled_sync,
    run_sync_once,
)
from realtor_agent.validation import validate_raw_snapshots

DEFAULT_DB_PATH = Path("data/realtor_agent.db")
DEFAULT_SYNC_HITS_PER_PAGE = 1000


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch BCFSA realtor data from Algolia.")
    parser.add_argument("--query", default="", help="Search text, for example 'smith'.")
    parser.add_argument("--page", type=int, default=0, help="Algolia page number.")
    parser.add_argument("--hits-per-page", type=int, default=10, help="Number of records to fetch.")
    parser.add_argument("--all", action="store_true", help="Fetch all pages.")
    parser.add_argument("--max-pages", type=int, help="Safety limit when using --all.")
    parser.add_argument("--output", type=Path, help="Optional file path to save raw JSON.")
    parser.add_argument("--store-raw", action="store_true", help="Save raw response(s) to SQLite.")
    parser.add_argument("--validate-raw", action="store_true", help="Validate stored raw snapshots.")
    parser.add_argument("--normalize", action="store_true", help="Normalize valid raw snapshots.")
    parser.add_argument("--save-realtors", action="store_true", help="Save normalized rows to realtors.")
    parser.add_argument("--sync-now", action="store_true", help="Run the full sync pipeline once.")
    parser.add_argument("--scheduled-sync", action="store_true", help="Run full sync now, then repeat.")
    parser.add_argument(
        "--sync-interval-hours",
        type=float,
        default=DEFAULT_SYNC_INTERVAL_HOURS,
        help="Hours between scheduled sync runs.",
    )
    parser.add_argument(
        "--sync-hits-per-page",
        type=int,
        default=DEFAULT_SYNC_HITS_PER_PAGE,
        help="Records per BCFSA API page during full sync.",
    )
    parser.add_argument(
        "--min-full-sync-record-ratio",
        type=float,
        default=DEFAULT_MIN_FULL_SYNC_RECORD_RATIO,
        help="Skip removal detection when a full sync returns below this ratio of the baseline.",
    )
    parser.add_argument("--dashboard", action="store_true", help="Start the local dashboard.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH, help="SQLite database path.")
    parser.add_argument("--host", default=DEFAULT_DASHBOARD_HOST, help="Dashboard host.")
    parser.add_argument("--port", type=int, default=DEFAULT_DASHBOARD_PORT, help="Dashboard port.")
    args = parser.parse_args()

    if args.dashboard:
        run_dashboard(args.db_path, host=args.host, port=args.port)
        return

    if args.scheduled_sync:
        run_scheduled_sync(
            db_path=args.db_path,
            interval_hours=args.sync_interval_hours,
            query=args.query,
            hits_per_page=args.sync_hits_per_page,
            max_pages=args.max_pages,
            min_full_sync_record_ratio=args.min_full_sync_record_ratio,
        )
        return

    if args.sync_now:
        summary = run_sync_once(
            db_path=args.db_path,
            query=args.query,
            hits_per_page=args.sync_hits_per_page,
            max_pages=args.max_pages,
            min_full_sync_record_ratio=args.min_full_sync_record_ratio,
            trigger="manual",
        )
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
            f"Change events: {summary.realtor_save.change_events_created}."
        )
        if summary.safety_warning:
            print(f"Warning: {summary.safety_warning}")
        return

    if args.save_realtors:
        summary = save_realtors_from_normalized(args.db_path)
        print(
            "Saved realtor records from normalized rows. "
            f"Checked: {summary.normalized_rows_checked}. "
            f"Saved/updated: {summary.realtor_rows_saved}. "
            f"Removed: {summary.removed_realtors}. "
            f"Total realtors: {summary.total_realtors}. "
            f"Change events: {summary.change_events_created}."
        )
        return

    if args.normalize:
        summary = normalize_raw_snapshots(args.db_path)
        print(
            "Normalized "
            f"{summary.normalized_records} record(s). "
            f"Checked: {summary.records_checked}. "
            f"Skipped: {summary.skipped_records}."
        )
        print(f"Normalized rows were stored in normalized_realtors inside {args.db_path}")
        return

    if args.validate_raw:
        summary = validate_raw_snapshots(args.db_path)
        print(
            "Validated "
            f"{summary.records_checked} record(s) "
            f"from {summary.snapshots_checked} raw snapshot(s). "
            f"Valid: {summary.valid_records}. "
            f"Invalid: {summary.invalid_records}."
        )
        print(f"Errors were stored in normalization_errors inside {args.db_path}")
        return

    adapter = BCFSAAlgoliaAdapter()
    if args.store_raw:
        store = RawSnapshotStore(args.db_path)
        pages = (
            adapter.fetch_pages(
                query=args.query,
                hits_per_page=args.hits_per_page,
                max_pages=args.max_pages,
            )
            if args.all
            else [
                adapter.fetch_page(
                    query=args.query,
                    page=args.page,
                    hits_per_page=args.hits_per_page,
                )
            ]
        )
        count = 0
        for page in pages:
            store.save(page)
            count += 1
        print(f"Stored {count} raw snapshot(s) in {args.db_path}")
        return

    if args.all:
        raw_response = adapter.fetch_all(
            query=args.query,
            hits_per_page=args.hits_per_page,
            max_pages=args.max_pages,
        )
    else:
        raw_page = adapter.fetch_page(
            query=args.query,
            page=args.page,
            hits_per_page=args.hits_per_page,
        )
        raw_response = raw_page.raw_json

    output = json.dumps(raw_response, indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
        print(f"Saved raw response to {args.output}")
        return

    print(output)


if __name__ == "__main__":
    main()
