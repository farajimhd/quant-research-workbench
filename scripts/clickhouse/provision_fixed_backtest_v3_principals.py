"""Operator-only fixed Backtest V3 principal plan; dry run by default.

The --apply path is intentionally separate and requires a second explicit
confirmation. This module does not provision Keeper ACLs or create arte tables.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import platform
import re
import secrets
import socket
import sys
from typing import Any, Callable
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from research.mlops.clickhouse import ClickHouseHttpClient
from scripts.clickhouse.provision_trading_journal import (
    SECRET_ROOT, SYSTEM_READ_TABLES, _admin_client, _restrict_secret_file,
)
from src.backend.backtest_fixed_v3_preflight import (
    policy_catalog_v3_contracts, read_v3_preflight, running_v3_contracts, running_v3_preflight,
    terminal_v3_contracts, terminal_v3_preflight,
)
from src.trading_runtime.arte_journal_schema import MARKET_READ_TABLES, storage_preflight
from src.trading_runtime.arte_market_day_certification import TABLES as MARKET_DAY_CERTIFICATE_TABLES
from src.backend.backtest_trade_proposal_v3 import TABLES as TRADE_PROPOSAL_TABLES
from src.backend.backtest_squeeze_episode_schema import (
    BROKER_OMS_TABLES, ENTRY_REPRICE_CAPACITY_TABLES, ENTRY_REPRICE_REJECTED,
    PROTECTED_EXIT_SATISFIED, PROTECTION_CHANGE_TABLES,
    PROTECTED_EXIT_SNAPSHOT,
    PORTFOLIO_ALLOCATION_FILL,
)


URL = "http://DESKTOP-SAAI85T:18123"
WORKSTATION_IPV4 = "192.168.1.218"
PRINCIPALS = {
    "read": "backtest_v3_reader",
    "running": "backtest_v3_runner",
    "terminal": "backtest_v3_terminal",
}
_USER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class PrincipalPlan:
    role: str
    principal: str
    select_arte: frozenset[str]
    insert_arte: frozenset[str]
    select_system: frozenset[str]
    select_reference: frozenset[tuple[str, str]] = frozenset()

    def grants(self) -> tuple[str, ...]:
        return tuple(
            [f"GRANT SELECT ON arte.{name} TO {self.principal}"
             for name in sorted(self.select_arte)] +
            [f"GRANT INSERT ON arte.{name} TO {self.principal}"
             for name in sorted(self.insert_arte)] +
            [f"GRANT SELECT ON {database}.{table} TO {self.principal}"
             for database, table in sorted(self.select_reference)] +
            [f"GRANT SELECT ON system.{name} TO {self.principal}"
             for name in sorted(self.select_system)]
        )


def desired_plan() -> tuple[PrincipalPlan, PrincipalPlan, PrincipalPlan]:
    """Derive exact table names from the same contracts as V3 preflight."""
    from src.backend.backtest_squeeze_episode_schema import (
        PORTFOLIO_CONTROL, RECONCILIATION_DIFFERENCE, RESERVATION_REASON,
        SQUEEZE_COMMIT_V3, SQUEEZE_EPISODE,
    )
    from src.backend.backtest_terminal_v3_dispatch import _TABLES
    from src.trading_runtime.arte_journal_writer import _FAMILIES, _profile_table

    running = frozenset(table.name for table in running_v3_contracts())
    terminal = frozenset(table.name for table in terminal_v3_contracts())
    running_insert = frozenset(_profile_table(table, "backtest_v3")
                               for table, _, _, _ in _FAMILIES) | frozenset({
                                   SQUEEZE_EPISODE.name, RESERVATION_REASON.name,
                                   RECONCILIATION_DIFFERENCE.name,
                                   PORTFOLIO_CONTROL.name,
                                   SQUEEZE_COMMIT_V3.name}) | frozenset(
                                       table.name for table in policy_catalog_v3_contracts())
    running_insert |= frozenset(table.name for table in (
        *TRADE_PROPOSAL_TABLES, *BROKER_OMS_TABLES,
        *ENTRY_REPRICE_CAPACITY_TABLES, ENTRY_REPRICE_REJECTED,
        PROTECTED_EXIT_SATISFIED, *PROTECTION_CHANGE_TABLES,
        PROTECTED_EXIT_SNAPSHOT, PORTFOLIO_ALLOCATION_FILL))
    terminal_insert = frozenset(_TABLES)
    if not running_insert <= running or not terminal_insert <= terminal:
        raise RuntimeError("V3 preflight references an unprovisioned table")
    system = frozenset(SYSTEM_READ_TABLES)
    return (
        PrincipalPlan("read", PRINCIPALS["read"],
                      terminal | MARKET_READ_TABLES |
                      frozenset(table.name for table in MARKET_DAY_CERTIFICATE_TABLES),
                      frozenset(), system,
                      frozenset({("q_live", "market_stock_split_v1")})),
        PrincipalPlan("running", PRINCIPALS["running"], running | MARKET_READ_TABLES,
                      running_insert, system),
        PrincipalPlan("terminal", PRINCIPALS["terminal"], terminal | MARKET_READ_TABLES,
                      terminal_insert, system),
    )


def render_plan(plans: tuple[PrincipalPlan, ...], *, stream: Any) -> None:
    print("DRY RUN - fixed Backtest V3 ClickHouse principals", file=stream)
    for plan in plans:
        print(f"{plan.role:8} {plan.principal:24} "
              f"arte SELECT {len(plan.select_arte):3}  "
              f"arte INSERT {len(plan.insert_arte):3}  "
              f"reference SELECT {len(plan.select_reference):2}  "
              f"system SELECT {len(plan.select_system):2}", file=stream)
    print("No connection, credential, DDL, or grant change was made.", file=stream)
    print("Apply requires --apply --confirm-v3-principals on DESKTOP-SAAI85T.",
          file=stream)


def _secret_path(role: str) -> Path:
    if role not in PRINCIPALS:
        raise ValueError("Unknown V3 principal role")
    return SECRET_ROOT / f"backtest_v3_{role}.env"


def _private_credential(plan: PrincipalPlan, *, account_exists: bool) -> str:
    """Reuse workstation private ACL practice; never rotate an existing file."""
    path = _secret_path(plan.role)
    user_key = f"BACKTEST_V3_{plan.role.upper()}_CLICKHOUSE_USER"
    pass_key = f"BACKTEST_V3_{plan.role.upper()}_CLICKHOUSE_PASSWORD"
    if path.exists():
        _restrict_secret_file(path)
        values = dict(line.split("=", 1) for line in
                      path.read_text(encoding="utf-8").splitlines() if "=" in line)
        password = values.get(pass_key, "")
        url_key = f"BACKTEST_V3_{plan.role.upper()}_CLICKHOUSE_URL"
        if (values.get(url_key) != URL or values.get(user_key) != plan.principal
                or len(password) < 40):
            raise RuntimeError("Existing V3 credential is incomplete or belongs to another principal")
        return password
    if account_exists:
        raise RuntimeError("Existing V3 principal lacks its private credential; refusing rotation")
    with path.open("x", encoding="utf-8"):
        pass
    _restrict_secret_file(path)
    password = secrets.token_urlsafe(48)
    path.write_text(
        f"BACKTEST_V3_{plan.role.upper()}_CLICKHOUSE_URL={URL}\n"
        f"{user_key}={plan.principal}\n{pass_key}={password}\n",
        encoding="utf-8")
    _restrict_secret_file(path)
    return password


def apply_with_clients(
    plans: tuple[PrincipalPlan, ...], *, admin: Any,
    credential: Callable[..., str],
    client_factory: Callable[[str, str], Any],
) -> None:
    """Testable apply core; CLI is the only real-client caller."""
    names = [plan.principal for plan in plans]
    if (len(plans) != 3 or len(set(names)) != 3
            or {plan.role for plan in plans} != set(PRINCIPALS)
            or any(PRINCIPALS[plan.role] != plan.principal
                   or _USER.fullmatch(plan.principal) is None for plan in plans)
            or plans != desired_plan()):
        raise ValueError("V3 provisioning plan differs from exact preflight contracts")
    admin_name = admin.execute("SELECT currentUser()").strip()
    if not admin_name or admin_name in names:
        raise RuntimeError("Distinct ClickHouse administrator is required")
    present_by_role: dict[str, bool] = {}
    for plan in plans:
        present = admin.execute(
            "SELECT count() FROM system.users "
            f"WHERE name='{plan.principal}' FORMAT TabSeparated").strip()
        if present not in {"0", "1"}:
            raise RuntimeError("V3 principal inventory is inconsistent")
        present_by_role[plan.role] = present == "1"
    storage_preflight(admin, tables=terminal_v3_contracts())
    passwords = {plan.role: credential(plan, account_exists=present_by_role[plan.role])
                 for plan in plans}
    if (any(not isinstance(password, str) or len(password) < 40
            for password in passwords.values())
            or len(set(passwords.values())) != 3):
        raise RuntimeError("V3 principals require three distinct private credentials")
    existing_clients: dict[str, Any] = {}
    existing_grants: dict[str, frozenset[tuple[str, str, str]]] = {}
    # Authenticate and audit every pre-existing account before any CREATE or
    # GRANT. This rejects inherited roles and broad grants without revocation.
    for plan in plans:
        if not present_by_role[plan.role]:
            continue
        client = client_factory(plan.principal, passwords[plan.role])
        if client.execute("SELECT currentUser()").strip() != plan.principal:
            raise RuntimeError("Saved V3 credential authenticates as a different principal")
        existing_clients[plan.role] = client
        existing_grants[plan.role] = _effective_grants(client, plan)
    for plan in plans:
        password = passwords[plan.role]
        if present_by_role[plan.role]:
            client = existing_clients[plan.role]
            have = existing_grants[plan.role]
        else:
            digest = sha256(password.encode()).hexdigest()
            admin.execute(
                f"CREATE USER {plan.principal} IDENTIFIED WITH sha256_hash BY '{digest}' "
                "HOST IP '172.16.0.0/12', IP '127.0.0.1', IP '::1'")
            client = client_factory(plan.principal, password)
            if client.execute("SELECT currentUser()").strip() != plan.principal:
                raise RuntimeError("New V3 credential authenticates as a different principal")
            have = frozenset()
        for privilege, database, table in sorted(_desired_grants(plan) - have):
            admin.execute(f"GRANT {privilege} ON {database}.{table} TO {plan.principal}")
        if plan.role == "read":
            read_v3_preflight(client)
        elif plan.role == "running":
            running_v3_preflight(client)
        else:
            terminal_v3_preflight(client)


def _desired_grants(plan: PrincipalPlan) -> frozenset[tuple[str, str, str]]:
    return frozenset(
        {("SELECT", "arte", name) for name in plan.select_arte} |
        {("INSERT", "arte", name) for name in plan.insert_arte} |
        {("SELECT", database, table) for database, table in plan.select_reference} |
        {("SELECT", "system", name) for name in plan.select_system}
    )


def _effective_grants(client: Any, plan: PrincipalPlan) -> frozenset[tuple[str, str, str]]:
    lines = client.execute("SHOW GRANTS FINAL").splitlines()
    desired = _desired_grants(plan)
    effective: set[tuple[str, str, str]] = set()
    for line in lines:
        match = re.fullmatch(
            r"GRANT ([A-Z ,]+) ON ([A-Za-z_][A-Za-z0-9_]*|\*)\."
            r"([A-Za-z_][A-Za-z0-9_]*|\*) TO ([A-Za-z_][A-Za-z0-9_]*)",
            line.strip())
        if match is None or match.group(4) != plan.principal:
            raise RuntimeError("Existing V3 principal has unrecognized effective grants")
        for privilege in (part.strip() for part in match.group(1).split(",")):
            grant = (privilege, match.group(2), match.group(3))
            if grant not in desired:
                raise RuntimeError("Existing V3 principal has extra or broad authority")
            effective.add(grant)
    return frozenset(effective)


def _operator_apply(url: str, plans: tuple[PrincipalPlan, ...]) -> None:
    if platform.node().upper() != "DESKTOP-SAAI85T" or not SECRET_ROOT.is_dir():
        raise RuntimeError("V3 provisioning requires workstation and private secrets directory")
    parsed = urlsplit(url)
    if (parsed.scheme, parsed.hostname, parsed.port, parsed.path, parsed.query,
            parsed.fragment, parsed.username, parsed.password) != (
            "http", "desktop-saai85t", 18123, "", "", "", None, None):
        raise RuntimeError("Unexpected ClickHouse endpoint")
    # The workstation's hostname may resolve to an unreachable IPv6 link-local
    # address before its managed IPv4 listener. A provisioning campaign issues
    # many small grant calls, so each IPv6 SYN timeout compounds dramatically.
    # Keep the published credential URL unchanged, but pin this local operator
    # session to the workstation IPv4 explicitly supplied for this campaign.
    # The host has several WSL/VPN adapter addresses, so selecting an
    # arbitrary DNS A record would be unsafe.
    addresses = {result[4][0] for result in socket.getaddrinfo(
        parsed.hostname, parsed.port, family=socket.AF_INET,
        type=socket.SOCK_STREAM)}
    if WORKSTATION_IPV4 not in addresses:
        raise RuntimeError("Pinned workstation IPv4 is not in hostname resolution")
    transport_url = f"http://{WORKSTATION_IPV4}:{parsed.port}"
    admin = _admin_client(transport_url)
    apply_with_clients(plans, admin=admin, credential=_private_credential,
        client_factory=lambda user, password: ClickHouseHttpClient(
            transport_url, user, password, timeout_seconds=20))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=URL, help="managed workstation endpoint")
    parser.add_argument("--apply", action="store_true", help="create exactly three principals")
    parser.add_argument("--confirm-v3-principals", action="store_true",
                        help="required second confirmation for --apply")
    args = parser.parse_args(argv)
    plans = desired_plan()
    if not args.apply:
        render_plan(plans, stream=sys.stdout)
        return 0
    if not args.confirm_v3_principals:
        parser.error("--apply requires --confirm-v3-principals")
    try:
        _operator_apply(args.url, plans)
    except Exception as exc:
        # Client exceptions may include SQL or credential data; print only a
        # stable type and direct operators to inspect private workstation logs.
        print(f"V3 provisioning stopped: {type(exc).__name__}; partial users or grants may exist. "
              "Review private operator diagnostics before retry.",
              file=sys.stderr)
        return 1
    print("V3 principals reconciled and exact preflights passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
