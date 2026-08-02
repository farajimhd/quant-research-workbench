from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402
from services.reference_gateway.providers import MassiveReferenceClient  # noqa: E402
from services.reference_gateway.ticker_events import (  # noqa: E402
    ensure_ticker_event_schema,
    refresh_ticker_event_inventory,
    sync_ticker_events,
    ticker_event_audit,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Populate and reconcile Massive Ticker Events by stable provider entity. "
            "Coverage is checkpointed per entity, so interrupted runs resume safely."
        )
    )
    parser.add_argument("--database", default=os.environ.get("REFERENCE_CLICKHOUSE_WRITE_DATABASE") or "q_live", help="Write database.")
    parser.add_argument("--read-database", default=os.environ.get("REFERENCE_CLICKHOUSE_READ_DATABASE") or "q_live", help="Canonical identity database.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--mode", choices=["historical", "delta", "rolling", "reconcile"], default="historical")
    parser.add_argument("--max-entities", type=int, default=0, help="Zero means every due entity.")
    parser.add_argument("--stale-after-days", type=int, default=7)
    parser.add_argument("--request-min-interval-seconds", type=float, default=0.12)
    parser.add_argument("--identifier", action="append", default=[], help="Optional ticker or Composite FIGI restriction; repeatable.")
    parser.add_argument("--refresh-inventory", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--execute", action="store_true", help="Create tables and write inventory, events, coverage, and intervals.")
    parser.add_argument(
        "--output-root-win",
        default=os.environ.get("REFERENCE_GATEWAY_RUNTIME_ROOT_WIN") or "D:/TradingML/runtimes/reference_gateway/ticker_events",
    )
    return parser.parse_args()


def main() -> None:
    load_env_files(discover_env_files(REPO_ROOT), verbose=False)
    args = parse_args()
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    provider = MassiveReferenceClient(
        base_url=os.environ.get("MASSIVE_BASE_URL") or "https://api.massive.com",
        api_key=os.environ.get("MASSIVE_API_KEY") or "",
        page_limit=int(os.environ.get("REFERENCE_GATEWAY_ACTIVE_TICKER_PAGE_LIMIT") or 1_000),
        max_pages=int(os.environ.get("REFERENCE_GATEWAY_ACTIVE_TICKER_MAX_PAGES") or 1_000),
    )
    run_id = "ticker_events_" + args.mode + "_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    if args.execute:
        ensure_ticker_event_schema(
            client,
            database=args.database,
            storage_policy=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
        )
    inventory = None
    if args.refresh_inventory:
        inventory = refresh_ticker_event_inventory(
            client,
            provider,
            database=args.database,
            execute=args.execute,
            run_id=run_id,
            on_progress=print_progress,
        )
        if inventory.saturated:
            raise SystemExit("Massive ticker inventory saturated the configured page limit; refusing incomplete event coverage.")
    result = sync_ticker_events(
        client,
        provider,
        database=args.database,
        read_database=args.read_database,
        execute=args.execute,
        mode=args.mode,
        max_entities=args.max_entities,
        stale_after_days=args.stale_after_days,
        request_min_interval_seconds=args.request_min_interval_seconds,
        only_identifiers=args.identifier,
        run_id=run_id,
        on_progress=print_progress,
    )
    audits = ticker_event_audit(client, database=args.database, read_database=args.read_database) if args.execute else []
    summary = {
        "run_id": run_id,
        "database": args.database,
        "read_database": args.read_database,
        "execute": args.execute,
        "inventory": asdict(inventory) if inventory is not None else None,
        "sync": result.public_dict(),
        "audits": audits,
    }
    output_root = Path(args.output_root_win) / run_id
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / "ticker_event_sync_summary.json"
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({"summary": str(output_path), "status": result.status, "selected": result.selected_entities}, sort_keys=True))
    if result.failed_entities:
        raise SystemExit(1)


def print_progress(source: str, status: str, message: str, rows: int | None) -> None:
    row_text = "-" if rows is None else f"{rows:,}"
    print(f"{source} status={status} rows={row_text} {message}", flush=True)


if __name__ == "__main__":
    main()
