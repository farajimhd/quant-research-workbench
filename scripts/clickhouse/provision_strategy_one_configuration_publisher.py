"""Provision the sole publisher of immutable Strategy 1 configuration rows.

Dry-run by default. The private credential stays on the managed workstation.
The principal has SELECT/INSERT on exactly the two typed configuration tables;
it cannot modify bars, indicators, liquidity, journals, or other strategies.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import os
from pathlib import Path
import platform
import re
import secrets
import sys
import traceback

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from research.mlops.clickhouse import ClickHouseHttpClient
from scripts.clickhouse.provision_trading_journal import (
    SECRET_ROOT, _admin_client, _restrict_secret_file,
)
from src.trading_runtime.strategy_one_configuration_tree import (
    NODE_TABLE, RELEASE_TABLE, install_tables,
)


PRINCIPAL = "strategy_one_configuration_publisher"
SECRET_PATH = SECRET_ROOT / "strategy_one_configuration_publisher.env"
WORKSTATION_IPV4 = "192.168.1.218"
_TABLES = (NODE_TABLE, RELEASE_TABLE)
_GRANTS = frozenset((privilege, table) for privilege in ("SELECT", "INSERT")
                    for table in _TABLES)
_GRANT_PATTERN = re.compile(
    rf"GRANT ([A-Z ,]+) ON (arte\.[A-Za-z_][A-Za-z0-9_]*) TO {PRINCIPAL}\Z")


def _credential(*, account_exists: bool) -> str:
    if SECRET_PATH.exists():
        _restrict_secret_file(SECRET_PATH)
        values = dict(line.split("=", 1) for line in
                      SECRET_PATH.read_text(encoding="utf-8").splitlines()
                      if "=" in line)
        password = values.get("STRATEGY_ONE_CONFIGURATION_PASSWORD", "")
        if values:
            if (values.get("STRATEGY_ONE_CONFIGURATION_USER") != PRINCIPAL
                    or len(password) < 40):
                raise RuntimeError("Configuration publisher credential is incomplete")
            return password
        if account_exists or SECRET_PATH.stat().st_size != 0:
            raise RuntimeError("Configuration publisher lacks private credential")
    else:
        if account_exists:
            raise RuntimeError("Configuration publisher lacks private credential")
        with SECRET_PATH.open("x", encoding="utf-8"):
            pass
        _restrict_secret_file(SECRET_PATH)
    password = secrets.token_urlsafe(48)
    SECRET_PATH.write_text(
        f"STRATEGY_ONE_CONFIGURATION_USER={PRINCIPAL}\n"
        f"STRATEGY_ONE_CONFIGURATION_PASSWORD={password}\n", encoding="utf-8")
    _restrict_secret_file(SECRET_PATH)
    return password


def _grant_set(client) -> frozenset[tuple[str, str]]:
    grants = set()
    for line in client.execute("SHOW GRANTS FINAL").splitlines():
        match = _GRANT_PATTERN.fullmatch(line.strip())
        if match is None:
            raise RuntimeError("Configuration publisher has a broad grant")
        for privilege in (item.strip() for item in match.group(1).split(",")):
            if privilege not in {"SELECT", "INSERT"}:
                raise RuntimeError("Configuration publisher has a foreign privilege")
            grants.add((privilege, match.group(2)))
    if not grants <= _GRANTS:
        raise RuntimeError("Configuration publisher has a foreign table grant")
    return frozenset(grants)


def provision(admin, *, credential, client_factory) -> None:
    install_tables(admin)
    present = admin.execute(
        "SELECT count() FROM system.users "
        f"WHERE name='{PRINCIPAL}' FORMAT TabSeparated").strip()
    if present not in {"0", "1"}:
        raise RuntimeError("Configuration publisher user inventory is inconsistent")
    password = credential(account_exists=present == "1")
    if not isinstance(password, str) or len(password) < 40:
        raise RuntimeError("Configuration publisher password is incomplete")
    if present == "0":
        digest = sha256(password.encode()).hexdigest()
        admin.execute(
            f"CREATE USER {PRINCIPAL} IDENTIFIED WITH sha256_hash BY '{digest}' "
            "HOST IP '172.16.0.0/12', IP '127.0.0.1', IP '::1'")
    client = client_factory(PRINCIPAL, password)
    try:
        if client.execute("SELECT currentUser()").strip() != PRINCIPAL:
            raise RuntimeError("Configuration publisher authenticated as another user")
        existing = _grant_set(client)
        for privilege, table in sorted(_GRANTS - existing):
            admin.execute(f"GRANT {privilege} ON {table} TO {PRINCIPAL}")
        if _grant_set(client) != _GRANTS:
            raise RuntimeError("Configuration publisher grants did not reconcile")
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-configuration-publisher", action="store_true")
    args = parser.parse_args(argv)
    if not args.apply:
        print(f"DRY RUN: {PRINCIPAL}; SELECT/INSERT on two typed ARTE tables only.")
        return 0
    if not args.confirm_configuration_publisher:
        parser.error("--apply requires --confirm-configuration-publisher")
    if platform.node().upper() != "DESKTOP-SAAI85T" or not SECRET_ROOT.is_dir():
        print("Blocked: managed workstation/private secrets are required.", file=sys.stderr)
        return 1
    admin = None
    try:
        endpoint = f"http://{WORKSTATION_IPV4}:18123"
        admin = _admin_client(endpoint)
        provision(admin, credential=_credential,
                  client_factory=lambda user, password: ClickHouseHttpClient(
                      endpoint, user, password, timeout_seconds=20))
    except Exception as exc:
        frames = traceback.extract_tb(exc.__traceback__)
        stage = next((f"{frame.name}:{frame.lineno}" for frame in reversed(frames)
                      if frame.filename == __file__), "external_dependency")
        print(f"Configuration publisher provisioning stopped: {type(exc).__name__} "
              f"at {stage}; inspect private diagnostics.", file=sys.stderr)
        return 1
    finally:
        if admin is not None:
            admin.close()
    print("Strategy 1 configuration publisher authenticated with four exact grants.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
