"""Provision the opt-in Strategy 1 journal runner; dry run by default.

This principal can append normalized V4 commits and their typed detail rows,
but can only SELECT market products and legacy run-context tables. It has no
CREATE, ALTER, DROP, or market INSERT authority. Credentials stay on the
workstation with a private ACL; the command never prints them.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import os
from pathlib import Path
import platform
import re
import secrets
import socket
import sys
import traceback
from typing import Any, Callable
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from research.mlops.clickhouse import ClickHouseHttpClient
from scripts.clickhouse.provision_fixed_backtest_v3_principals import (
    PrincipalPlan, URL, WORKSTATION_IPV4, _desired_grants, _effective_grants,
)
from scripts.clickhouse.provision_trading_journal import (
    SECRET_ROOT, SYSTEM_READ_TABLES, _admin_client, _restrict_secret_file,
)
from src.trading_runtime.arte_journal_schema import (
    MARKET_READ_TABLES, V4_COMMIT_TABLES, V4_ORDER_COMMAND_LINEAGE,
    fixed_backtest_v2_contracts,
    storage_preflight,
)
from src.trading_runtime.arte_journal_writer import (
    _FAMILIES, _v4_family_table, _v4_preflight, v4_journal_write_tables,
)
from src.trading_runtime.arte_journal_schema import PORTFOLIO_SNAPSHOT_WRITE_TABLES
from src.trading_runtime.arte_backtest_definition import TABLES as BACKTEST_DEFINITION_TABLES
from src.trading_runtime.arte_strategy_one_entry_schema import (
    ADD_EVIDENCE, ENTRY_EVIDENCE,
)
from src.trading_runtime.arte_broker_acknowledgement_v4 import ACKNOWLEDGEMENT
from src.trading_runtime.arte_order_cancel_v4 import CANCEL
from src.trading_runtime.arte_order_reprice_v4 import REPRICE
from src.trading_runtime.arte_portfolio_allocation_v4 import ALLOCATION as V4_ALLOCATION
from src.trading_runtime.arte_reservation_reason_v4 import RESERVATION_REASON
from src.trading_runtime.arte_oms_tactic_schema import TABLES as OMS_TACTIC_TABLES
from src.trading_runtime.arte_risk_action_v4 import TABLES as RISK_ACTION_TABLES
from src.trading_runtime.arte_protection_reconciliation_v4 import (
    TABLES as PROTECTION_RECONCILIATION_TABLES,
)
from src.backend.backtest_protection_change_v3 import TABLES as PROTECTION_CHANGE_TABLES
from src.trading_runtime.strategy_one_management_snapshot import (
    TABLES as MANAGER_SNAPSHOT_TABLES,
)
from src.trading_runtime.strategy_one_broker_match_snapshot import (
    TABLES as BROKER_MATCH_SNAPSHOT_TABLES,
)
from src.trading_runtime.strategy_one_evidence_snapshot import (
    TABLES as EVIDENCE_SNAPSHOT_TABLES,
)
from src.trading_runtime.strategy_one_campaign_snapshot import (
    TABLES as CAMPAIGN_SNAPSHOT_TABLES,
)
from src.trading_runtime.strategy_one_oms_observation_snapshot import (
    TABLES as OMS_OBSERVATION_SNAPSHOT_TABLES,
)
from src.trading_runtime.strategy_one_protection_snapshot import (
    TABLES as PROTECTION_SNAPSHOT_TABLES,
)
from src.backend.strategy_one_entry_context import TABLE as ENTRY_CONTEXT_TABLE


PRINCIPAL = "backtest_v4_runner"
SECRET_PATH = SECRET_ROOT / "backtest_v4_runner.env"
URL_KEY = "BACKTEST_V4_RUNNER_CLICKHOUSE_URL"
USER_KEY = "BACKTEST_V4_RUNNER_CLICKHOUSE_USER"
PASSWORD_KEY = "BACKTEST_V4_RUNNER_CLICKHOUSE_PASSWORD"

_RETIRED_SNAPSHOTS = frozenset({
    "trading_strategy_one_manager_snapshot_v1",
    "trading_strategy_one_manager_source_v1",
    "trading_strategy_one_manager_pending_break_v1",
    "trading_strategy_one_broker_match_snapshot_v1",
    "trading_strategy_one_broker_match_account_v1",
    "trading_strategy_one_broker_match_position_v1",
    "trading_strategy_one_broker_match_open_order_v1",
    "trading_strategy_one_broker_match_ticker_v1",
    "trading_strategy_one_broker_match_performance_mark_v1",
    "trading_strategy_one_broker_match_snapshot_v2",
    "trading_strategy_one_broker_match_account_v2",
    "trading_strategy_one_broker_match_position_v2",
    "trading_strategy_one_broker_match_open_order_v2",
    "trading_strategy_one_broker_match_ticker_v2",
    "trading_strategy_one_broker_match_performance_mark_v2",
    "trading_strategy_one_broker_match_snapshot_v3",
    "trading_strategy_one_broker_match_account_v3",
    "trading_strategy_one_broker_match_position_v3",
    "trading_strategy_one_broker_match_open_order_v3",
    "trading_strategy_one_broker_match_ticker_v3",
    "trading_strategy_one_broker_match_performance_mark_v3",
    "trading_strategy_one_broker_match_snapshot_v4",
    "trading_strategy_one_broker_match_account_v4",
    "trading_strategy_one_broker_match_position_v4",
    "trading_strategy_one_broker_match_open_order_v4",
    "trading_strategy_one_broker_match_ticker_v4",
    "trading_strategy_one_broker_match_performance_mark_v4",
    "trading_strategy_one_oms_observation_snapshot_v1",
    "trading_strategy_one_oms_observation_v1",
})


def _retire_snapshot_grants(admin: Any, writer: Any) -> None:
    """Retire superseded app-owned snapshots; keep market ACL intact."""
    observed: set[tuple[str, str]] = set()
    for line in writer.execute("SHOW GRANTS FINAL").splitlines():
        match = re.fullmatch(
            rf"GRANT ([A-Z ,]+) ON arte\.([a-z][a-z0-9_]*) TO {PRINCIPAL}",
            line.strip())
        if match is None or match.group(2) not in _RETIRED_SNAPSHOTS:
            continue
        for privilege in match.group(1).split(","):
            privilege = privilege.strip()
            if privilege not in {"SELECT", "INSERT"}:
                raise RuntimeError("Retired broker table has unexpected authority")
            observed.add((privilege, match.group(2)))
    for privilege, table in sorted(observed):
        admin.execute(f"REVOKE {privilege} ON arte.{table} FROM {PRINCIPAL}")


def desired_plan() -> PrincipalPlan:
    writable = v4_journal_write_tables()
    return PrincipalPlan(
        "running", PRINCIPAL,
        frozenset(table.name for table in (*fixed_backtest_v2_contracts(), *V4_COMMIT_TABLES,
                                          V4_ORDER_COMMAND_LINEAGE,
                                          ENTRY_EVIDENCE, ADD_EVIDENCE,
                                          V4_ALLOCATION,
                                          RESERVATION_REASON,
                                          ACKNOWLEDGEMENT, CANCEL,
                                          REPRICE,
                                          *OMS_TACTIC_TABLES,
                                          *RISK_ACTION_TABLES,
                                          *PROTECTION_CHANGE_TABLES,
                                          *PROTECTION_RECONCILIATION_TABLES,
                                          *PROTECTION_SNAPSHOT_TABLES,
                                          *MANAGER_SNAPSHOT_TABLES,
                                          *BROKER_MATCH_SNAPSHOT_TABLES,
                                          *EVIDENCE_SNAPSHOT_TABLES,
                                          *CAMPAIGN_SNAPSHOT_TABLES,
                                          *OMS_OBSERVATION_SNAPSHOT_TABLES,
                                          *BACKTEST_DEFINITION_TABLES))
        | MARKET_READ_TABLES
        | frozenset({ENTRY_CONTEXT_TABLE.split(".", 1)[1]}),
        writable, frozenset(SYSTEM_READ_TABLES),
    )


def _credential(*, account_exists: bool) -> str:
    if SECRET_PATH.exists():
        _restrict_secret_file(SECRET_PATH)
        values = dict(line.split("=", 1) for line in
                      SECRET_PATH.read_text(encoding="utf-8").splitlines()
                      if "=" in line)
        password = values.get(PASSWORD_KEY, "")
        if (values.get(URL_KEY) != URL or values.get(USER_KEY) != PRINCIPAL
                or len(password) < 40):
            raise RuntimeError("V4 credential is incomplete or belongs to another principal")
        return password
    if account_exists:
        raise RuntimeError("Existing V4 principal lacks its private credential")
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
    """Reconcile only missing exact grants; reject existing extra authority."""
    plan = desired_plan()
    if admin.execute("SELECT currentUser()").strip() == PRINCIPAL:
        raise RuntimeError("V4 provisioning requires a distinct administrator")
    present = admin.execute(
        "SELECT count() FROM system.users "
        f"WHERE name='{PRINCIPAL}' FORMAT TabSeparated").strip()
    if present not in {"0", "1"}:
        raise RuntimeError("V4 principal inventory is inconsistent")
    storage_preflight(admin, tables=fixed_backtest_v2_contracts())
    storage_preflight(admin, tables=V4_COMMIT_TABLES)
    storage_preflight(admin, tables=(V4_ORDER_COMMAND_LINEAGE,))
    storage_preflight(admin, tables=(ENTRY_EVIDENCE, ADD_EVIDENCE))
    storage_preflight(admin, tables=OMS_TACTIC_TABLES)
    storage_preflight(admin, tables=BACKTEST_DEFINITION_TABLES)
    storage_preflight(admin, tables=PROTECTION_SNAPSHOT_TABLES)
    storage_preflight(admin, tables=MANAGER_SNAPSHOT_TABLES)
    storage_preflight(admin, tables=BROKER_MATCH_SNAPSHOT_TABLES)
    password = credential(account_exists=present == "1")
    if not isinstance(password, str) or len(password) < 40:
        raise RuntimeError("V4 requires a private credential of at least 40 characters")
    writer = client_factory(PRINCIPAL, password)
    try:
        if present == "1":
            if writer.execute("SELECT currentUser()").strip() != PRINCIPAL:
                raise RuntimeError("Saved V4 credential authenticates as another principal")
            _retire_snapshot_grants(admin, writer)
            have = _effective_grants(writer, plan)
        else:
            digest = sha256(password.encode()).hexdigest()
            admin.execute(
                f"CREATE USER {PRINCIPAL} IDENTIFIED WITH sha256_hash BY '{digest}' "
                "HOST IP '172.16.0.0/12', IP '127.0.0.1', IP '::1'")
            if writer.execute("SELECT currentUser()").strip() != PRINCIPAL:
                raise RuntimeError("New V4 credential authenticates as another principal")
            have = frozenset()
        for privilege, database, table in sorted(_desired_grants(plan) - have):
            admin.execute(f"GRANT {privilege} ON {database}.{table} TO {PRINCIPAL}")
        _v4_preflight(writer)
    finally:
        close = getattr(writer, "close", None)
        if close is not None:
            close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=URL, help="managed workstation endpoint")
    parser.add_argument("--apply", action="store_true", help="create or reconcile exact grants")
    parser.add_argument("--confirm-v4-runner", action="store_true",
                        help="required confirmation for --apply")
    args = parser.parse_args(argv)
    plan = desired_plan()
    print(f"V4 runner: {plan.principal}; arte SELECT {len(plan.select_arte)}, "
          f"arte INSERT {len(plan.insert_arte)}, "
          f"system SELECT {len(plan.select_system)}")
    if not args.apply:
        print("Plan only; no connection, credential, grant, or row changed")
        return 0
    if not args.confirm_v4_runner:
        parser.error("--apply requires --confirm-v4-runner")
    try:
        parsed = urlsplit(args.url)
        if (platform.node().upper() != "DESKTOP-SAAI85T"
                or not SECRET_ROOT.is_dir()
                or (parsed.scheme, parsed.hostname, parsed.port, parsed.path,
                    parsed.query, parsed.fragment, parsed.username, parsed.password)
                != ("http", "desktop-saai85t", 18123, "", "", "", None, None)):
            raise RuntimeError("V4 provisioning requires the managed workstation endpoint")
        addresses = {row[4][0] for row in socket.getaddrinfo(
            parsed.hostname, parsed.port, family=socket.AF_INET,
            type=socket.SOCK_STREAM)}
        if WORKSTATION_IPV4 not in addresses:
            raise RuntimeError("Pinned workstation IPv4 is not in hostname resolution")
        transport = f"http://{WORKSTATION_IPV4}:{parsed.port}"
        admin = _admin_client(transport)
        try:
            apply_with_clients(
                admin=admin, credential=_credential,
                client_factory=lambda user, password: ClickHouseHttpClient(
                    transport, user, password, timeout_seconds=20))
        finally:
            admin.close()
    except Exception as exc:
        frames = traceback.extract_tb(exc.__traceback__)
        stage = next((f"{frame.name}:{frame.lineno}" for frame in reversed(frames)
                      if frame.filename == __file__), "external_dependency")
        print(f"V4 runner provisioning stopped: {type(exc).__name__} at {stage}; "
              "partial grants may exist; rerun after private diagnosis.", file=sys.stderr)
        return 1
    print("V4 runner authenticated; exact grants and SSD placement verified; 0 rows inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
