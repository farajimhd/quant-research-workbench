"""Install only the normalized Strategy 1 live modify-command table.

Dry-run by default. This never inserts rows, touches market products, or
grants runtime DDL. The separate live-principal provisioner reconciles grants
after this operator-owned layout is verified.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import socket
import sys
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.provision_fixed_backtest_v3_principals import (
    URL, WORKSTATION_IPV4,
)
from scripts.clickhouse.provision_trading_journal import SECRET_ROOT, _admin_client
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.arte_order_modify_command_v1 import MODIFY_COMMAND


def reconcile_layout(admin, *, apply: bool) -> bool:
    """Return whether CREATE ran; reject incompatible existing data/layout."""
    policy = admin.execute(
        "SELECT count() FROM system.storage_policies "
        "WHERE policy_name='live_market_ssd' FORMAT TabSeparated").strip()
    if policy != "1":
        raise RuntimeError("Required live_market_ssd policy is unavailable")
    rows = admin.execute(
        "SELECT count() FROM system.tables "
        "WHERE database='arte' AND name='trading_order_modify_command_v1' "
        "FORMAT TabSeparated").strip()
    if rows not in {"0", "1"}:
        raise RuntimeError("Modify-command table inventory is ambiguous")
    if rows == "1":
        storage_preflight(admin, tables=(MODIFY_COMMAND,))
        return False
    if not apply:
        return False
    admin.execute(MODIFY_COMMAND.ddl())
    storage_preflight(admin, tables=(MODIFY_COMMAND,))
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-modify-command", action="store_true")
    args = parser.parse_args(argv)
    print("Strategy 1 modify-command layout: one normalized live_market_ssd table")
    if not args.apply:
        print("Plan only; no connection, table, grant, or row changed")
        return 0
    if not args.confirm_modify_command:
        parser.error("--apply requires --confirm-modify-command")
    try:
        parsed = urlsplit(URL)
        if (platform.node().upper() != "DESKTOP-SAAI85T"
                or not SECRET_ROOT.is_dir()
                or (parsed.scheme, parsed.hostname, parsed.port, parsed.path,
                    parsed.query, parsed.fragment, parsed.username, parsed.password)
                   != ("http", "desktop-saai85t", 18123, "", "", "", None, None)):
            raise RuntimeError("Modify-command layout requires the managed workstation")
        addresses = {row[4][0] for row in socket.getaddrinfo(
            parsed.hostname, parsed.port, family=socket.AF_INET,
            type=socket.SOCK_STREAM)}
        if WORKSTATION_IPV4 not in addresses:
            raise RuntimeError("Pinned workstation IPv4 is not in hostname resolution")
        admin = _admin_client(f"http://{WORKSTATION_IPV4}:{parsed.port}")
        try:
            created = reconcile_layout(admin, apply=True)
        finally:
            admin.close()
    except Exception as exc:
        frames = traceback.extract_tb(exc.__traceback__)
        stage = next((f"{frame.name}:{frame.lineno}" for frame in reversed(frames)
                     if frame.filename == __file__), "external_dependency")
        print(f"Modify-command provisioning stopped: {type(exc).__name__} "
              f"at {stage}", file=sys.stderr)
        return 1
    print(f"Modify-command SSD layout verified; created={created}; 0 rows inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
