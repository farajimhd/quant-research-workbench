from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.reference_data.sec_issuer_relationships import (  # noqa: E402
    DEFAULT_RELATIONSHIP_PATH,
    DEFAULT_RELATIONSHIP_TABLE,
    active_parent_listing_count_sql,
    insert_relationships,
    load_relationship_definitions,
    relationship_table_ddl,
    resolve_relationships,
)
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
)
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and publish curated SEC filing-issuer to listed-parent relationships.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("REFERENCE_GATEWAY_WRITE_DATABASE", "q_live"))
    parser.add_argument("--table", default=os.environ.get("SEC_ISSUER_RELATIONSHIP_TABLE", DEFAULT_RELATIONSHIP_TABLE))
    parser.add_argument("--relationships-json", default=str(DEFAULT_RELATIONSHIP_PATH))
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY", ""))
    parser.add_argument("--output-root-win", default="D:/market-data/prepared/reference_gateway/sec_issuer_relationships")
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    definitions = load_relationship_definitions(Path(args.relationships_json))
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    resolved = resolve_relationships(client, database=args.database, definitions=definitions)
    run_id = "sec_issuer_relationship_sync_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    report = {
        "run_id": run_id,
        "status": "dry_run",
        "database": args.database,
        "table": args.table,
        "relationship_count": len(resolved),
        "relationships": [
            {
                "relationship_id": row.relationship_id,
                "child_cik": row.child_cik,
                "parent_cik": row.parent_cik,
                "relationship_type": row.relationship_type,
                "child_issuer_id": row.child_issuer_id,
                "parent_issuer_id": row.parent_issuer_id,
            }
            for row in resolved
        ],
    }
    if args.execute:
        client.execute(relationship_table_ddl(args.database, args.table, args.storage_policy))
        inserted_rows, deactivated_rows = insert_relationships(
            client,
            database=args.database,
            table=args.table,
            relationships=resolved,
            run_id=run_id,
        )
        logical_rows = int(
            client.execute(
                f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(args.table)} FINAL FORMAT TSV"
            ).strip()
            or 0
        )
        parent_listing_rows = int(client.execute(active_parent_listing_count_sql(args.database, args.table) + "\nFORMAT TSV").strip() or 0)
        if logical_rows < len(resolved) or parent_listing_rows < len(resolved):
            raise RuntimeError(
                f"relationship publication validation failed: logical_rows={logical_rows}, "
                f"parent_listing_rows={parent_listing_rows}, expected_at_least={len(resolved)}"
            )
        report.update(
            status="ok",
            logical_rows=logical_rows,
            active_parent_listing_rows=parent_listing_rows,
            inserted_rows=inserted_rows,
            deactivated_rows=deactivated_rows,
        )
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / f"{run_id}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("summary=" + json.dumps({**report, "report_path": str(report_path)}, ensure_ascii=True, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
