"""Operator-only removal of the four retired opaque ARTE Backtest tables.

Dry-run is the default. This command never touches market products, the typed
trading journal, filesystem artifacts, or credentials. Run it only after all
workstation jobs using the retired tables have stopped.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import sys
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.provision_trading_journal import _admin_client


URL = "http://DESKTOP-SAAI85T:18123"
RETIRED = (
    "bt_commit_v1", "bt_event_v1", "bt_blob_v1", "bt_run_v1",
)


def retire(client: object, *, apply: bool) -> tuple[str, ...]:
    """Inspect exact names and remove only known retired MergeTree tables."""
    raw = client.execute(
        "SELECT name,engine,storage_policy FROM system.tables "
        "WHERE database='arte' AND startsWith(name,'bt_') FORMAT JSONEachRow"
    )
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    by_name = {str(row["name"]): row for row in rows}
    if len(by_name) != len(rows) or set(by_name) - set(RETIRED):
        raise RuntimeError("Unexpected or duplicated arte.bt_* table; nothing was dropped")
    if any(row["engine"] != "MergeTree" for row in rows):
        raise RuntimeError("Retired table engine differs; nothing was dropped")
    present = tuple(name for name in RETIRED if name in by_name)
    if not apply:
        return present
    for name in present:
        client.execute(f"DROP TABLE arte.{name} SYNC")
        remaining = client.execute(
            "SELECT count() FROM system.tables WHERE database='arte' "
            f"AND name='{name}' FORMAT TabSeparated"
        ).strip()
        if remaining != "0":
            raise RuntimeError(f"Retired table {name} did not disappear after DROP")
    return present


def main() -> int:
    parser = argparse.ArgumentParser(description="Drop only the four retired arte.bt_* tables")
    parser.add_argument("--apply", action="store_true", help="perform the exact table drops")
    parser.add_argument("--url", default=URL)
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if (platform.node().upper() != "DESKTOP-SAAI85T"
            or (parsed.scheme, parsed.hostname, parsed.port, parsed.path,
                parsed.query, parsed.fragment, parsed.username, parsed.password)
            != ("http", "desktop-saai85t", 18123, "", "", "", None, None)):
        parser.error("Use the managed workstation and its exact ClickHouse endpoint")
    client = _admin_client(args.url)
    try:
        names = retire(client, apply=args.apply)
    finally:
        client.close()
    print(("Dropped" if args.apply else "Would drop") + ": "
          + (", ".join(f"arte.{name}" for name in names) if names else "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
