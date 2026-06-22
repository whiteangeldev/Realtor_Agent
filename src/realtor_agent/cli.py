import argparse
import json
from pathlib import Path

from realtor_agent.raw_snapshot_store import RawSnapshotStore
from realtor_agent.source_adapters import BCFSAAlgoliaAdapter
from realtor_agent.validation import validate_raw_snapshots

DEFAULT_DB_PATH = Path("data/realtor_agent.db")


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
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH, help="SQLite database path.")
    args = parser.parse_args()

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
