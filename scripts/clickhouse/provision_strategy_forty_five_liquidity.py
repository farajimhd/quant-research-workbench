"""Operator plan for exactly two Strategy 45 source tables and narrow grants.

Dry-run is the default. Applying requires the workstation, its private secret
root and an explicit privilege confirmation. Existing products and identities
are never dropped, truncated, redefined, revoked or credential-rotated.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import os
from pathlib import Path
import platform
import re
import secrets
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.mlops.clickhouse import ClickHouseHttpClient
from scripts.clickhouse.install_market_day_certificate_layout import workstation_clickhouse_url
from scripts.clickhouse.provision_trading_journal import SECRET_ROOT, _admin_client, _restrict_secret_file
from src.trading_runtime.strategy_forty_five_liquidity_schema import CONTRACTS, ddl, install_tables, verify_tables

PRINCIPAL = "strategy_forty_five_producer"
READER = "backtest_v3_reader"
SECRET_PATH = SECRET_ROOT / "strategy_forty_five_producer.env"
URL = "http://DESKTOP-SAAI85T:18123"
GRANTS = frozenset((privilege, table) for table in CONTRACTS for privilege in ("SELECT", "INSERT"))


def statements():
    return (*ddl(), *(f"GRANT SELECT,INSERT ON {table} TO {PRINCIPAL}" for table in CONTRACTS),
            *(f"GRANT SELECT ON {table} TO {READER}" for table in CONTRACTS))


def grant_set(client):
    pattern = re.compile(rf"GRANT ([A-Z ,]+) ON (arte\.[A-Za-z0-9_]+) TO {PRINCIPAL}\Z")
    result = set()
    for line in client.execute("SHOW GRANTS FINAL").splitlines():
        match = pattern.fullmatch(line.strip())
        if match is None:
            raise RuntimeError("Strategy 45 producer has unexpected authority")
        result.update((privilege.strip(), match[2]) for privilege in match[1].split(","))
    return frozenset(result)


def credential(*, account_exists):
    if SECRET_PATH.exists():
        values = {}
        for line in SECRET_PATH.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in values:
                raise RuntimeError("Strategy 45 private credential shape differs")
            values[key] = value
        if (set(values) != {"STRATEGY_FORTY_FIVE_URL", "STRATEGY_FORTY_FIVE_USER", "STRATEGY_FORTY_FIVE_PASSWORD"}
                or values["STRATEGY_FORTY_FIVE_URL"] != URL
                or values["STRATEGY_FORTY_FIVE_USER"] != PRINCIPAL
                or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", values["STRATEGY_FORTY_FIVE_PASSWORD"])):
            raise RuntimeError("Strategy 45 private credential identity differs")
        _restrict_secret_file(SECRET_PATH)
        return values["STRATEGY_FORTY_FIVE_PASSWORD"]
    if account_exists:
        raise RuntimeError("Existing Strategy 45 producer lacks its retained private credential")
    password = secrets.token_urlsafe(48)
    with SECRET_PATH.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(f"STRATEGY_FORTY_FIVE_URL={URL}\nSTRATEGY_FORTY_FIVE_USER={PRINCIPAL}\nSTRATEGY_FORTY_FIVE_PASSWORD={password}\n")
    _restrict_secret_file(SECRET_PATH)
    return password


def apply():
    if platform.node().upper() != "DESKTOP-SAAI85T" or not SECRET_ROOT.is_dir():
        raise RuntimeError("Strategy 45 provisioning requires the workstation private secret root")
    transport = workstation_clickhouse_url()
    with closing(_admin_client(transport)) as admin:
        # Validate layout before giving any principal new authority.
        install_tables(admin)
        if admin.execute(f"SELECT count() FROM system.users WHERE name='{READER}'").strip() != "1":
            raise RuntimeError("Existing read-only Backtest principal is required")
        exists = admin.execute(f"SELECT count() FROM system.users WHERE name='{PRINCIPAL}'").strip()
        if exists not in ("0", "1"):
            raise RuntimeError("Strategy 45 producer identity is ambiguous")
        password = credential(account_exists=exists == "1")
        if exists == "0":
            # Generated password is restricted to the validated SQL-safe alphabet.
            admin.execute(f"CREATE USER {PRINCIPAL} IDENTIFIED WITH sha256_password BY '{password}'")
        with closing(ClickHouseHttpClient(transport, PRINCIPAL, password, timeout_seconds=60)) as producer:
            if producer.execute("SELECT currentUser()").strip() != PRINCIPAL or not grant_set(producer) <= GRANTS:
                raise RuntimeError("Strategy 45 producer identity or existing grants differ")
            for table in CONTRACTS:
                admin.execute(f"GRANT SELECT,INSERT ON {table} TO {PRINCIPAL}")
                admin.execute(f"GRANT SELECT ON {table} TO {READER}")
            if grant_set(producer) != GRANTS:
                raise RuntimeError("Strategy 45 producer exact grants differ after provisioning")
        verify_tables(admin)
        from src.backend.backtest_v3_clients import v3_client
        with closing(v3_client("read", environment={"BACKTEST_V3_READ_CREDENTIAL_FILE":
                str(SECRET_ROOT / "backtest_v3_read.env")})) as reader:
            if reader.execute("SELECT currentUser()").strip() != READER:
                raise RuntimeError("Strategy 45 Backtest reader identity differs")
            verify_tables(reader)
            for table in CONTRACTS:
                reader.execute(f"SELECT count() FROM {table}")
    print("Verified two SSD source tables, four producer privileges and two Backtest SELECT grants.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-source-grants", action="store_true")
    args = parser.parse_args()
    if args.apply != args.confirm_source_grants:
        parser.error("Apply requires both --apply and --confirm-source-grants")
    if args.apply:
        apply()
    else:
        print("Plan only; no tables, users, credentials, grants or source rows changed.")
        for statement in statements():
            print(statement)
        print(f"Dedicated producer: {PRINCIPAL}; retained credential only under workstation secrets.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Do not expose SQL text, passwords or credential contents on failures.
        print(f"Strategy 45 provisioning failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1)
