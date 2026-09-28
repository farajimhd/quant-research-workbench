"""Provision the dedicated Strategy 1 Live V4 journal principal.

Dry-run by default. This never creates tables or inserts rows. The live order
admission gate remains closed until cold recovery and broker parity are wired.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import os
from pathlib import Path
import platform
import secrets
import socket
import sys
import traceback
from typing import Any, Callable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from research.mlops.clickhouse import ClickHouseHttpClient
from scripts.clickhouse.provision_fixed_backtest_v3_principals import (
    URL, WORKSTATION_IPV4, _desired_grants, _effective_grants,
)
from scripts.clickhouse.provision_trading_journal import (
    SECRET_ROOT, _admin_client, _restrict_secret_file,
)
from src.backend.live_strategy_one_v4_principal import (
    PRINCIPAL, _CONFIG_READ, desired_plan, live_v4_preflight,
    live_v4_storage_contracts,
)
from src.backend.live_strategy_one_approval import TABLE as APPROVAL
from src.trading_runtime.arte_journal_schema import MARKET_READ_TABLES, storage_preflight
from src.trading_runtime.strategy_one_configuration_tree import verify_tables


SECRET_PATH = SECRET_ROOT / "strategy_one_live_v4_runner.env"
URL_KEY = "STRATEGY_ONE_LIVE_V4_CLICKHOUSE_URL"
USER_KEY = "STRATEGY_ONE_LIVE_V4_CLICKHOUSE_USER"
PASSWORD_KEY = "STRATEGY_ONE_LIVE_V4_CLICKHOUSE_PASSWORD"


def _credential(*, account_exists: bool) -> str:
    if SECRET_PATH.exists():
        _restrict_secret_file(SECRET_PATH)
        values = dict(line.split("=", 1) for line in
                      SECRET_PATH.read_text(encoding="utf-8").splitlines()
                      if "=" in line)
        password = values.get(PASSWORD_KEY, "")
        if (values.get(URL_KEY) != URL or values.get(USER_KEY) != PRINCIPAL
                or len(password) < 40):
            raise RuntimeError("Existing Live V4 credential does not match its principal")
        return password
    if account_exists:
        raise RuntimeError("Existing Live V4 principal lacks its private credential")
    with SECRET_PATH.open("x", encoding="utf-8"):
        pass
    _restrict_secret_file(SECRET_PATH)
    password = secrets.token_urlsafe(48)
    SECRET_PATH.write_text(
        f"{URL_KEY}={URL}\n{USER_KEY}={PRINCIPAL}\n{PASSWORD_KEY}={password}\n",
        encoding="utf-8")
    _restrict_secret_file(SECRET_PATH)
    return password


def apply_with_clients(*, admin: Any, credential: Callable[..., str],
                       client_factory: Callable[[str, str], Any]) -> None:
    """Reconcile missing exact grants; refuse extra authority or rotation."""
    plan = desired_plan()
    if admin.execute("SELECT currentUser()").strip() == PRINCIPAL:
        raise RuntimeError("Live V4 provisioning needs a distinct administrator")
    present = admin.execute(
        "SELECT count() FROM system.users "
        f"WHERE name='{PRINCIPAL}' FORMAT TabSeparated").strip()
    if present not in {"0", "1"}:
        raise RuntimeError("Live V4 principal inventory is inconsistent")
    contracts = {table.name: table for table in live_v4_storage_contracts()}
    names = plan.select_arte & contracts.keys()
    if plan.select_arte - names != MARKET_READ_TABLES | _CONFIG_READ:
        raise RuntimeError("Live V4 principal has an unmodeled storage grant")
    storage_preflight(admin, tables=tuple(contracts[name] for name in sorted(names)))
    verify_tables(admin)
    storage_preflight(admin, tables=(APPROVAL,))
    password = credential(account_exists=present == "1")
    if not isinstance(password, str) or len(password) < 40:
        raise RuntimeError("Live V4 needs a private credential of at least 40 characters")
    writer = client_factory(PRINCIPAL, password)
    try:
        if present == "1":
            if writer.execute("SELECT currentUser()").strip() != PRINCIPAL:
                raise RuntimeError("Saved Live V4 credential authenticates as another principal")
            have = _effective_grants(writer, plan)
        else:
            digest = sha256(password.encode()).hexdigest()
            admin.execute(
                f"CREATE USER {PRINCIPAL} IDENTIFIED WITH sha256_hash BY '{digest}' "
                "HOST IP '172.16.0.0/12', IP '127.0.0.1', IP '::1'")
            if writer.execute("SELECT currentUser()").strip() != PRINCIPAL:
                raise RuntimeError("New Live V4 credential authenticates as another principal")
            have = frozenset()
        for privilege, database, table in sorted(_desired_grants(plan) - have):
            admin.execute(f"GRANT {privilege} ON {database}.{table} TO {PRINCIPAL}")
        live_v4_preflight(writer)
    finally:
        close = getattr(writer, "close", None)
        if callable(close):
            close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=URL)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-live-v4-runner", action="store_true")
    args = parser.parse_args(argv)
    plan = desired_plan()
    print(f"Live V4 runner: {plan.principal}; arte SELECT {len(plan.select_arte)}, "
          f"arte INSERT {len(plan.insert_arte)}, "
          f"reference SELECT {len(plan.select_reference)}")
    if not args.apply:
        print("Plan only; no connection, credential, grant, or row changed")
        return 0
    if not args.confirm_live_v4_runner:
        parser.error("--apply requires --confirm-live-v4-runner")
    try:
        parsed = urlsplit(args.url)
        if (platform.node().upper() != "DESKTOP-SAAI85T"
                or not SECRET_ROOT.is_dir()
                or (parsed.scheme, parsed.hostname, parsed.port, parsed.path,
                    parsed.query, parsed.fragment, parsed.username, parsed.password)
                != ("http", "desktop-saai85t", 18123, "", "", "", None, None)):
            raise RuntimeError("Live V4 provisioning requires the managed workstation")
        addresses = {row[4][0] for row in socket.getaddrinfo(
            parsed.hostname, parsed.port, family=socket.AF_INET,
            type=socket.SOCK_STREAM)}
        if WORKSTATION_IPV4 not in addresses:
            raise RuntimeError("Pinned workstation IPv4 is not in hostname resolution")
        admin = _admin_client(f"http://{WORKSTATION_IPV4}:{parsed.port}")
        try:
            apply_with_clients(
                admin=admin, credential=_credential,
                client_factory=lambda user, password: ClickHouseHttpClient(
                    f"http://{WORKSTATION_IPV4}:{parsed.port}", user, password,
                    timeout_seconds=20))
        finally:
            admin.close()
    except Exception as exc:
        frames = traceback.extract_tb(exc.__traceback__)
        stage = next((f"{frame.name}:{frame.lineno}" for frame in reversed(frames)
                      if frame.filename == __file__), "external_dependency")
        print(f"Live V4 provisioning stopped: {type(exc).__name__} at {stage}; "
              "partial grants may exist; rerun after private diagnosis.", file=sys.stderr)
        return 1
    print("Live V4 principal authenticated; exact grants and SSD verified; 0 rows inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
