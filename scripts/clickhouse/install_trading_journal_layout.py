"""Install normalized ARTE journal DDL; never insert rows.

The default is a read-only plan. An interrupted --apply is restart-safe: each
installed table is checked against its exact contract before work continues.
The journal writer principal has no CREATE privilege; this is operator-owned.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import socket
import sys
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.plan_trading_journal_layout import plan_missing, profile_contracts
from scripts.clickhouse.provision_trading_journal import _admin_client
from src.trading_runtime.arte_journal_schema import (
    STORAGE_POLICY, TableContract, fixed_backtest_v2_contracts, storage_preflight,
)
from src.backend.backtest_squeeze_episode_schema import (
    BROKER_OMS_TABLES, ENTRY_REPRICE_CAPACITY_TABLES, ENTRY_REPRICE_REJECTED,
    PROTECTED_EXIT_SATISFIED,
    PORTFOLIO_CONTROL,
    RECONCILIATION_DIFFERENCE, RESERVATION_REASON,
    SQUEEZE_COMMIT_V3, staged_portfolio_control_ddl,
    staged_reconciliation_difference_ddl, staged_reservation_reason_ddl,
    staged_trade_proposal_ddl, staged_broker_oms_ddl,
    staged_entry_reprice_capacity_ddl, staged_entry_reprice_rejected_ddl,
    staged_protected_exit_satisfied_ddl,
)
from src.backend.backtest_trade_proposal_v3 import TABLES as TRADE_PROPOSAL_TABLES
from src.backend.live_plan_membership import TABLES as LIVE_PLAN_MEMBERSHIP_TABLES
from scripts.clickhouse.provision_fixed_backtest_v3_principals import WORKSTATION_IPV4


_REASON_COLUMNS = frozenset({"portfolio_reservation_reason_count",
                             "portfolio_reservation_reason_hash"})
_RECONCILIATION_COLUMNS = frozenset({
    "portfolio_reconciliation_difference_count",
    "portfolio_reconciliation_difference_hash",
})
_CONTROL_COLUMNS = frozenset({"portfolio_control_count", "portfolio_control_hash"})
_PROPOSAL_COLUMNS = frozenset({"trade_proposal_child_count", "trade_proposal_child_hash"})
_BROKER_OMS_COLUMNS = frozenset({
    "broker_short_order_skip_count", "broker_short_order_skip_hash",
    "broker_reply_policy_event_count", "broker_reply_policy_event_hash",
    "broker_reply_policy_message_count", "broker_reply_policy_message_hash",
    "entry_reprice_deferred_count", "entry_reprice_deferred_hash",
})
_CAPACITY_COLUMNS = frozenset({
    "entry_reprice_capacity_count", "entry_reprice_capacity_hash",
    "entry_reprice_capacity_reason_count", "entry_reprice_capacity_reason_hash",
})
_REJECTED_COLUMNS = frozenset({
    "entry_reprice_rejected_count", "entry_reprice_rejected_hash",
})
_SATISFIED_COLUMNS = frozenset({
    "protected_exit_satisfied_count", "protected_exit_satisfied_hash",
})


def upgrade_v3_protected_exit_satisfied(client: object, *, apply: bool) -> str:
    """Install closed OMS fact only after an exact zero-row V3 fence proof."""
    actual = tuple((row["name"], row["type"]) for row in (
        json.loads(line) for line in client.execute(
            "SELECT name,type FROM system.columns WHERE database='arte' "
            "AND table='trading_commit_v3' ORDER BY position FORMAT JSONEachRow"
        ).splitlines() if line.strip()))
    full = SQUEEZE_COMMIT_V3.columns
    start = next(i for i, (name, _) in enumerate(full)
                 if name == "protected_exit_satisfied_count")
    suffix = full[start:start + 2]
    if actual not in {full[:start] + suffix[:i] + full[-3:]
                      for i in range(len(suffix) + 1)}:
        raise RuntimeError("V3 protected-exit commit suffix is not exact")
    present = len(actual) - (len(full) - len(suffix))
    storage_preflight(client, tables=(TableContract(
        SQUEEZE_COMMIT_V3.name, actual, SQUEEZE_COMMIT_V3.partition,
        SQUEEZE_COMMIT_V3.order),))
    exists = client.execute(
        "SELECT count() FROM system.tables WHERE database='arte' "
        f"AND name='{PROTECTED_EXIT_SATISFIED.name}'").strip()
    if exists not in {"0", "1"}:
        raise RuntimeError("V3 protected-exit table inventory is ambiguous")
    if exists == "1":
        storage_preflight(client, tables=(PROTECTED_EXIT_SATISFIED,))
    if present == len(suffix) and exists == "1":
        return "verified"
    if client.execute("SELECT count() FROM arte.trading_commit_v3").strip() != "0":
        raise RuntimeError("V3 commit has rows; versioned migration required")
    if exists == "1" and client.execute(
            f"SELECT count() FROM arte.{PROTECTED_EXIT_SATISFIED.name}").strip() != "0":
        raise RuntimeError("V3 protected-exit fact has rows; no ALTER attempted")
    if not apply:
        return "planned"
    ddls = staged_protected_exit_satisfied_ddl()
    if exists == "0":
        client.execute(ddls[0])
        storage_preflight(client, tables=(PROTECTED_EXIT_SATISFIED,))
    for ddl in ddls[1 + present:]:
        client.execute(ddl)
    storage_preflight(client, tables=(PROTECTED_EXIT_SATISFIED, SQUEEZE_COMMIT_V3))
    return "upgraded"


def upgrade_v3_entry_reprice_rejected(client: object, *, apply: bool) -> str:
    """Install or correct the empty refusal fact under an exact V3 fence."""
    actual = tuple((row["name"], row["type"]) for row in (
        json.loads(line) for line in client.execute(
            "SELECT name,type FROM system.columns WHERE database='arte' "
            "AND table='trading_commit_v3' ORDER BY position FORMAT JSONEachRow"
        ).splitlines() if line.strip()))
    full = SQUEEZE_COMMIT_V3.columns
    start = next(i for i, (name, _) in enumerate(full)
                 if name == "entry_reprice_rejected_count")
    suffix = full[start:start + 2]
    if actual not in {full[:start] + suffix[:i] + full[-3:]
                      for i in range(len(suffix) + 1)} | {full}:
        raise RuntimeError("V3 refusal commit suffix is not exact")
    present = sum(name in {column[0] for column in suffix} for name, _ in actual)
    storage_preflight(client, tables=(TableContract(
        SQUEEZE_COMMIT_V3.name, actual, SQUEEZE_COMMIT_V3.partition,
        SQUEEZE_COMMIT_V3.order),))
    exists = client.execute(
        "SELECT count() FROM system.tables WHERE database='arte' "
        f"AND name='{ENTRY_REPRICE_REJECTED.name}'").strip()
    if exists not in {"0", "1"}:
        raise RuntimeError("V3 refusal table inventory is ambiguous")
    pending_decimal_columns: tuple[str, ...] = ()
    if exists == "1":
        child_columns = tuple((row["name"], row["type"]) for row in (
            json.loads(line) for line in client.execute(
                "SELECT name,type FROM system.columns WHERE database='arte' "
                f"AND table='{ENTRY_REPRICE_REJECTED.name}' "
                "ORDER BY position FORMAT JSONEachRow"
            ).splitlines() if line.strip()))
        old_columns = tuple((name, "Float64" if name in {
            "price", "remaining_quantity"} else kind)
            for name, kind in ENTRY_REPRICE_REJECTED.columns)
        if child_columns == ENTRY_REPRICE_REJECTED.columns:
            storage_preflight(client, tables=(ENTRY_REPRICE_REJECTED,))
        elif child_columns in (old_columns, tuple(
                (name, "Decimal(38, 18)" if name == "price" else kind)
                for name, kind in old_columns)):
            pending_decimal_columns = tuple(name for name in (
                "price", "remaining_quantity") if dict(child_columns)[name] == "Float64")
            storage_preflight(client, tables=(TableContract(
                ENTRY_REPRICE_REJECTED.name, child_columns,
                ENTRY_REPRICE_REJECTED.partition,
                ENTRY_REPRICE_REJECTED.order),))
        else:
            raise RuntimeError("V3 refusal fact columns differ from known contracts")
    if present == len(suffix) and exists == "1" and not pending_decimal_columns:
        return "verified"
    if client.execute("SELECT count() FROM arte.trading_commit_v3").strip() != "0":
        raise RuntimeError("V3 commit has rows; versioned migration required")
    if exists == "1" and client.execute(
            f"SELECT count() FROM arte.{ENTRY_REPRICE_REJECTED.name}").strip() != "0":
        raise RuntimeError("V3 refusal fact has rows; no ALTER attempted")
    if not apply:
        return "planned"
    ddls = staged_entry_reprice_rejected_ddl()
    if exists == "0":
        client.execute(ddls[0])
        storage_preflight(client, tables=(ENTRY_REPRICE_REJECTED,))
    elif pending_decimal_columns:
        for name in pending_decimal_columns:
            if client.execute(
                    f"SELECT count() FROM arte.{ENTRY_REPRICE_REJECTED.name}").strip() != "0":
                raise RuntimeError("V3 refusal fact became occupied before decimal ALTER")
            client.execute(
                f"ALTER TABLE arte.{ENTRY_REPRICE_REJECTED.name} "
                f"MODIFY COLUMN {name} Decimal(38, 18)")
        storage_preflight(client, tables=(ENTRY_REPRICE_REJECTED,))
    for ddl in ddls[1 + present:]:
        client.execute(ddl)
    storage_preflight(client, tables=(ENTRY_REPRICE_REJECTED, TableContract(
        SQUEEZE_COMMIT_V3.name,
        full if actual == full else full[:start] + suffix + full[-3:],
        SQUEEZE_COMMIT_V3.partition, SQUEEZE_COMMIT_V3.order)))
    return "upgraded"


def install_live_plan_membership(client: object, *, apply: bool) -> str:
    """Install or safely upgrade empty typed control-plane tables; no rows."""
    names = ",".join(f"'{table.name}'" for table in LIVE_PLAN_MEMBERSHIP_TABLES)
    result = [json.loads(line) for line in client.execute(
        "SELECT name FROM system.tables WHERE database='arte' "
        f"AND name IN ({names}) FORMAT JSONEachRow").splitlines() if line.strip()]
    installed = {row["name"] for row in result}
    if (len(installed) != len(result) or
            any(set(row) != {"name"} for row in result)):
        raise RuntimeError("Live membership table inventory is ambiguous")
    legacy = []
    for table in LIVE_PLAN_MEMBERSHIP_TABLES:
        if table.name not in installed:
            continue
        columns = tuple((row["name"], row["type"]) for row in (
            json.loads(line) for line in client.execute(
                "SELECT name,type FROM system.columns WHERE database='arte' "
                f"AND table='{table.name}' ORDER BY position FORMAT JSONEachRow"
            ).splitlines() if line.strip()))
        old_columns = tuple(column for column in table.columns
                            if column[0] != "publication_id")
        if columns == table.columns:
            storage_preflight(client, tables=(table,))
        elif columns == old_columns:
            storage_preflight(client, tables=(TableContract(
                table.name, old_columns, table.partition, table.order),))
            if client.execute(f"SELECT count() FROM arte.{table.name}").strip() != "0":
                raise RuntimeError("Occupied membership table requires versioned migration")
            legacy.append(table)
        else:
            raise RuntimeError(f"Live membership columns differ: {table.name}")
    if len(installed) == len(LIVE_PLAN_MEMBERSHIP_TABLES) and not legacy:
        return "verified"
    if not apply:
        return "planned"
    for table in LIVE_PLAN_MEMBERSHIP_TABLES:
        if table.name not in installed:
            client.execute(table.ddl())
            storage_preflight(client, tables=(table,))
        elif table in legacy:
            if client.execute(f"SELECT count() FROM arte.{table.name}").strip() != "0":
                raise RuntimeError("Membership table became occupied before ALTER")
            client.execute(
                f"ALTER TABLE arte.{table.name} ADD COLUMN IF NOT EXISTS "
                "publication_id UUID AFTER membership_sequence")
            storage_preflight(client, tables=(table,))
    return "upgraded"


def upgrade_v3_entry_reprice_capacity(client: object, *, apply: bool) -> str:
    """Resume closed capacity child/fence DDL only while V3 is empty."""
    actual = tuple((row["name"], row["type"]) for row in (
        json.loads(line) for line in client.execute(
            "SELECT name,type FROM system.columns WHERE database='arte' "
            "AND table='trading_commit_v3' ORDER BY position FORMAT JSONEachRow"
        ).splitlines() if line.strip()))
    full = SQUEEZE_COMMIT_V3.columns
    start = next(i for i, (name, _) in enumerate(full)
                 if name == "entry_reprice_capacity_count")
    suffix = full[start:start + 4]
    if actual not in {full[:start] + suffix[:i] + full[-3:]
                      for i in range(len(suffix) + 1)} | {full}:
        raise RuntimeError("V3 capacity commit suffix is not exact")
    present = sum(name in {column[0] for column in suffix} for name, _ in actual)
    storage_preflight(client, tables=(TableContract(
        SQUEEZE_COMMIT_V3.name, actual, SQUEEZE_COMMIT_V3.partition,
        SQUEEZE_COMMIT_V3.order),))
    names = ",".join(f"'{table.name}'" for table in ENTRY_REPRICE_CAPACITY_TABLES)
    rows = [json.loads(line) for line in client.execute(
        "SELECT name FROM system.tables WHERE database='arte' "
        f"AND name IN ({names}) FORMAT JSONEachRow").splitlines() if line.strip()]
    installed = {row["name"] for row in rows}
    if len(installed) != len(rows) or any(set(row) != {"name"} for row in rows):
        raise RuntimeError("V3 capacity table inventory is ambiguous")
    for table in ENTRY_REPRICE_CAPACITY_TABLES:
        if table.name in installed:
            storage_preflight(client, tables=(table,))
    if present == len(suffix) and len(installed) == len(ENTRY_REPRICE_CAPACITY_TABLES):
        return "verified"
    if client.execute("SELECT count() FROM arte.trading_commit_v3").strip() != "0":
        raise RuntimeError("V3 commit has rows; versioned migration required")
    for table in ENTRY_REPRICE_CAPACITY_TABLES:
        if table.name in installed and client.execute(
                f"SELECT count() FROM arte.{table.name}").strip() != "0":
            raise RuntimeError("V3 capacity child has rows; no ALTER attempted")
    if not apply:
        return "planned"
    ddls = staged_entry_reprice_capacity_ddl()
    for table, ddl in zip(ENTRY_REPRICE_CAPACITY_TABLES, ddls):
        if table.name not in installed:
            client.execute(ddl)
            storage_preflight(client, tables=(table,))
    for ddl in ddls[len(ENTRY_REPRICE_CAPACITY_TABLES) + present:]:
        client.execute(ddl)
    storage_preflight(client, tables=ENTRY_REPRICE_CAPACITY_TABLES +
                      (TableContract(SQUEEZE_COMMIT_V3.name,
                       full if actual == full else full[:start] + suffix + full[-3:],
                       SQUEEZE_COMMIT_V3.partition, SQUEEZE_COMMIT_V3.order),))
    return "upgraded"


def upgrade_v3_broker_oms(client: object, *, apply: bool) -> str:
    """Install closed broker/OMS children only behind a proved-empty V3 seal."""
    actual = tuple((row["name"], row["type"]) for row in (
        json.loads(line) for line in client.execute(
            "SELECT name,type FROM system.columns WHERE database='arte' "
            "AND table='trading_commit_v3' ORDER BY position FORMAT JSONEachRow"
        ).splitlines() if line.strip()))
    full = SQUEEZE_COMMIT_V3.columns
    start = next(i for i, (name, _) in enumerate(full)
                 if name == "broker_short_order_skip_count")
    suffix = full[start:start + 8]
    if actual not in {full[:start] + suffix[:i] + full[-3:]
                      for i in range(len(suffix) + 1)} | {
                          full[:-7] + full[-3:], full[:-5] + full[-3:], full}:
        raise RuntimeError("V3 broker/OMS commit suffix is not exact")
    present = sum(name in {column[0] for column in suffix} for name, _ in actual)
    storage_preflight(client, tables=(TableContract(
        SQUEEZE_COMMIT_V3.name, actual, SQUEEZE_COMMIT_V3.partition,
        SQUEEZE_COMMIT_V3.order),))
    names = ",".join(f"'{table.name}'" for table in BROKER_OMS_TABLES)
    rows = [json.loads(line) for line in client.execute(
        "SELECT name FROM system.tables WHERE database='arte' "
        f"AND name IN ({names}) FORMAT JSONEachRow").splitlines() if line.strip()]
    installed = {row["name"] for row in rows}
    if len(installed) != len(rows) or any(set(row) != {"name"} for row in rows):
        raise RuntimeError("V3 broker/OMS table inventory is ambiguous")
    for table in BROKER_OMS_TABLES:
        if table.name in installed:
            storage_preflight(client, tables=(table,))
    if present == len(suffix) and len(installed) == len(BROKER_OMS_TABLES):
        return "verified"
    if client.execute("SELECT count() FROM arte.trading_commit_v3").strip() != "0":
        raise RuntimeError("V3 commit has rows; versioned migration required")
    for table in BROKER_OMS_TABLES:
        if table.name in installed and client.execute(
                f"SELECT count() FROM arte.{table.name}").strip() != "0":
            raise RuntimeError("V3 broker/OMS child has rows; no ALTER attempted")
    if not apply:
        return "planned"
    ddls = staged_broker_oms_ddl()
    for table, ddl in zip(BROKER_OMS_TABLES, ddls):
        if table.name not in installed:
            client.execute(ddl)
            storage_preflight(client, tables=(table,))
    for ddl in ddls[len(BROKER_OMS_TABLES) + present:]:
        client.execute(ddl)
    storage_preflight(client, tables=BROKER_OMS_TABLES + (TableContract(
        SQUEEZE_COMMIT_V3.name,
        actual if len(actual) >= len(full) - len(_REJECTED_COLUMNS)
        - len(_SATISFIED_COLUMNS)
        else full[:start] + suffix + full[-3:],
        SQUEEZE_COMMIT_V3.partition,
        SQUEEZE_COMMIT_V3.order),))
    return "upgraded"


def _without_proposals(columns):
    return tuple(column for column in columns if column[0] not in
                 (_PROPOSAL_COLUMNS | _BROKER_OMS_COLUMNS | _CAPACITY_COLUMNS
                  | _REJECTED_COLUMNS | _SATISFIED_COLUMNS))


def _with_existing_proposals(columns, actual):
    """Preserve an already staged proposal suffix during older empty-fence upgrades."""
    broker_present = [name for name, _ in actual if name in _BROKER_OMS_COLUMNS]
    broker_expected = [name for name, _ in columns if name in _BROKER_OMS_COLUMNS]
    if broker_present != broker_expected[:len(broker_present)]:
        raise RuntimeError("V3 commit has an invalid broker/OMS suffix")
    capacity_present = [name for name, _ in actual if name in _CAPACITY_COLUMNS]
    capacity_expected = [name for name, _ in columns if name in _CAPACITY_COLUMNS]
    if capacity_present != capacity_expected[:len(capacity_present)]:
        raise RuntimeError("V3 commit has an invalid entry-reprice-capacity suffix")
    rejected_present = [name for name, _ in actual if name in _REJECTED_COLUMNS]
    rejected_expected = [name for name, _ in columns if name in _REJECTED_COLUMNS]
    if rejected_present != rejected_expected[:len(rejected_present)]:
        raise RuntimeError("V3 commit has an invalid entry-reprice-refusal suffix")
    satisfied_present = [name for name, _ in actual if name in _SATISFIED_COLUMNS]
    satisfied_expected = [name for name, _ in columns if name in _SATISFIED_COLUMNS]
    if satisfied_present != satisfied_expected[:len(satisfied_present)]:
        raise RuntimeError("V3 commit has an invalid protected-exit suffix")
    present = {name for name, _ in actual} & _PROPOSAL_COLUMNS
    if present not in (set(), {"trade_proposal_child_count"}, _PROPOSAL_COLUMNS):
        raise RuntimeError("V3 commit has an invalid trade-proposal suffix")
    return tuple(column for column in columns
                 if column[0] not in (_PROPOSAL_COLUMNS | _BROKER_OMS_COLUMNS
                                     | _CAPACITY_COLUMNS | _REJECTED_COLUMNS
                                     | _SATISFIED_COLUMNS)
                 or column[0] in present or column[0] in broker_present
                 or column[0] in capacity_present or column[0] in rejected_present
                 or column[0] in satisfied_present)


def upgrade_v3_portfolio_control(client: object, *, apply: bool) -> str:
    """Stage scalar control child only on a proved-empty V3 fence."""
    columns = [json.loads(line) for line in client.execute(
        "SELECT name,type FROM system.columns WHERE database='arte' "
        "AND table='trading_commit_v3' ORDER BY position FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    actual = tuple((row["name"], row["type"]) for row in columns)
    full = _with_existing_proposals(SQUEEZE_COMMIT_V3.columns, actual)
    old = tuple(column for column in full
                if column[0] not in {"portfolio_control_count", "portfolio_control_hash"})
    partial = tuple(column for column in full
                    if column[0] != "portfolio_control_hash")
    if actual not in {old, partial, full}:
        raise RuntimeError("V3 commit has an unknown control schema")
    storage_preflight(client, tables=(TableContract(
        SQUEEZE_COMMIT_V3.name, actual, SQUEEZE_COMMIT_V3.partition,
        SQUEEZE_COMMIT_V3.order),))
    exists = client.execute(
        "SELECT count() FROM system.tables WHERE database='arte' "
        "AND name='trading_portfolio_control_v3'").strip()
    if exists not in {"0", "1"}:
        raise RuntimeError("Portfolio control table catalog is ambiguous")
    if exists == "1":
        storage_preflight(client, tables=(PORTFOLIO_CONTROL,))
    if actual == full and exists == "1":
        return "verified"
    if client.execute("SELECT count() FROM arte.trading_commit_v3").strip() != "0":
        raise RuntimeError("V3 commit has rows; a versioned migration is required")
    if exists == "1" and client.execute(
            "SELECT count() FROM arte.trading_portfolio_control_v3").strip() != "0":
        raise RuntimeError("Portfolio control child has rows; no ALTER attempted")
    if not apply:
        return "planned"
    table_ddl, count_ddl, hash_ddl = staged_portfolio_control_ddl()
    if exists == "0":
        client.execute(table_ddl)
        storage_preflight(client, tables=(PORTFOLIO_CONTROL,))
    if actual == old:
        client.execute(count_ddl)
    if actual in {old, partial}:
        client.execute(hash_ddl)
    storage_preflight(client, tables=(PORTFOLIO_CONTROL, TableContract(
        SQUEEZE_COMMIT_V3.name, full, SQUEEZE_COMMIT_V3.partition,
        SQUEEZE_COMMIT_V3.order)))
    return "upgraded"


def upgrade_v3_trade_proposal(client: object, *, apply: bool) -> str:
    """Resume the nine child tables and two seal columns only on empty V3."""
    columns = [json.loads(line) for line in client.execute(
        "SELECT name,type FROM system.columns WHERE database='arte' "
        "AND table='trading_commit_v3' ORDER BY position FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    actual = tuple((row["name"], row["type"]) for row in columns)
    full = _with_existing_proposals(SQUEEZE_COMMIT_V3.columns, actual)
    old = _without_proposals(full)
    partial = tuple(column for column in full
                    if column[0] != "trade_proposal_child_hash")
    if actual not in {old, partial, full}:
        raise RuntimeError("V3 commit has an unknown trade-proposal schema")
    storage_preflight(client, tables=(TableContract(
        SQUEEZE_COMMIT_V3.name, actual, SQUEEZE_COMMIT_V3.partition,
        SQUEEZE_COMMIT_V3.order),))
    names = ",".join(f"'{table.name}'" for table in TRADE_PROPOSAL_TABLES)
    rows = [json.loads(line) for line in client.execute(
        "SELECT name FROM system.tables WHERE database='arte' "
        f"AND name IN ({names}) FORMAT JSONEachRow").splitlines() if line.strip()]
    installed = {row["name"] for row in rows}
    expected = {table.name for table in TRADE_PROPOSAL_TABLES}
    if (len(installed) != len(rows) or not installed <= expected
            or any(set(row) != {"name"} for row in rows)):
        raise RuntimeError("V3 proposal child inventory is ambiguous")
    for table in TRADE_PROPOSAL_TABLES:
        if table.name in installed:
            storage_preflight(client, tables=(table,))
    if actual == full and installed == expected:
        return "verified"
    if client.execute("SELECT count() FROM arte.trading_commit_v3").strip() != "0":
        raise RuntimeError("V3 commit has rows; versioned migration required")
    for table in TRADE_PROPOSAL_TABLES:
        if table.name in installed and client.execute(
                f"SELECT count() FROM arte.{table.name}").strip() != "0":
            raise RuntimeError("V3 proposal child has rows; no ALTER attempted")
    if not apply:
        return "planned"
    ddls = staged_trade_proposal_ddl()
    for table, ddl in zip(TRADE_PROPOSAL_TABLES, ddls):
        if table.name not in installed:
            client.execute(ddl)
            storage_preflight(client, tables=(table,))
    if actual == old:
        client.execute(ddls[-2])
    if actual in {old, partial}:
        client.execute(ddls[-1])
    storage_preflight(client, tables=TRADE_PROPOSAL_TABLES + (SQUEEZE_COMMIT_V3,))
    return "upgraded"


def upgrade_v3_reconciliation_difference(client: object, *, apply: bool) -> str:
    """Operator-only, restart-safe child/fence upgrade; never insert rows."""
    columns = [json.loads(line) for line in client.execute(
        "SELECT name,type FROM system.columns WHERE database='arte' "
        "AND table='trading_commit_v3' ORDER BY position FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    actual = tuple((row["name"], row["type"]) for row in columns)
    full = tuple(column for column in _with_existing_proposals(SQUEEZE_COMMIT_V3.columns, actual)
                 if column[0] not in _CONTROL_COLUMNS or
                 column[0] in {name for name, _ in actual})
    old = tuple(column for column in full
                if column[0] not in _RECONCILIATION_COLUMNS)
    partial = tuple(column for column in full
                    if column[0] != "portfolio_reconciliation_difference_hash")
    if actual not in {old, partial, full}:
        raise RuntimeError("V3 commit has an unknown schema; no ALTER attempted")
    shape = TableContract(SQUEEZE_COMMIT_V3.name, actual,
                          SQUEEZE_COMMIT_V3.partition, SQUEEZE_COMMIT_V3.order)
    storage_preflight(client, tables=(shape,))
    count = client.execute(
        "SELECT count() FROM system.tables WHERE database='arte' "
        "AND name='trading_portfolio_reconciliation_difference_v3'"
    ).strip()
    if count not in {"0", "1"}:
        raise RuntimeError("Reconciliation difference table catalog is ambiguous")
    child_present = count == "1"
    if child_present:
        storage_preflight(client, tables=(RECONCILIATION_DIFFERENCE,))
    if actual == full and child_present:
        print("V3 reconciliation differences: schema and SSD placement verified; no change")
        return "verified"
    if client.execute("SELECT count() FROM arte.trading_commit_v3").strip() != "0":
        raise RuntimeError("V3 commit has rows; versioned migration required")
    if child_present and client.execute(
            "SELECT count() FROM arte.trading_portfolio_reconciliation_difference_v3"
    ).strip() != "0":
        raise RuntimeError("Reconciliation difference child has rows; no ALTER attempted")
    pending = ["child table"] if not child_present else []
    if actual == old:
        pending.append("V3 difference count and hash")
    elif actual == partial:
        pending.append("V3 difference hash")
    print("V3 reconciliation differences: empty fence verified; pending "
          + ", ".join(pending))
    if not apply:
        print("Plan only; no ClickHouse state changed")
        return "planned"
    child_ddl, count_ddl, hash_ddl = staged_reconciliation_difference_ddl()
    if not child_present:
        client.execute(child_ddl)
        storage_preflight(client, tables=(RECONCILIATION_DIFFERENCE,))
    if actual == old:
        client.execute(count_ddl)
    if actual in {old, partial}:
        client.execute(hash_ddl)
    storage_preflight(client, tables=(RECONCILIATION_DIFFERENCE, TableContract(
        SQUEEZE_COMMIT_V3.name, full, SQUEEZE_COMMIT_V3.partition,
        SQUEEZE_COMMIT_V3.order)))
    print("V3 reconciliation differences: table and commit fence verified; 0 rows inserted")
    return "upgraded"


def upgrade_v3_reservation_reason(client: object, *, apply: bool) -> str:
    """Restart-safe operator DDL; no row INSERT and no occupied fence rewrite."""
    columns = [json.loads(line) for line in client.execute(
        "SELECT name,type FROM system.columns WHERE database='arte' "
        "AND table='trading_commit_v3' ORDER BY position FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    actual = tuple((row["name"], row["type"]) for row in columns)
    full = tuple(column for column in _with_existing_proposals(SQUEEZE_COMMIT_V3.columns, actual)
                 if column[0] not in _CONTROL_COLUMNS or
                 column[0] in {name for name, _ in actual})
    actual_core = tuple(column for column in actual
                        if column[0] not in _RECONCILIATION_COLUMNS)
    core_full = tuple(column for column in full
                      if column[0] not in _RECONCILIATION_COLUMNS)
    old = tuple(column for column in core_full if column[0] not in _REASON_COLUMNS)
    partial = tuple(column for column in core_full
                    if column[0] != "portfolio_reservation_reason_hash")
    difference_names = {name for name, _ in actual} & _RECONCILIATION_COLUMNS
    allowed_difference_names = (frozenset(),
        frozenset({"portfolio_reconciliation_difference_count"}),
        _RECONCILIATION_COLUMNS)
    if (actual_core not in {old, partial, core_full}
            or frozenset(difference_names) not in allowed_difference_names
            or actual != tuple(column for column in full
                               if column[0] in {name for name, _ in actual})):
        raise RuntimeError("V3 commit has an unknown schema; no ALTER attempted")
    shape = TableContract(SQUEEZE_COMMIT_V3.name, actual,
                          SQUEEZE_COMMIT_V3.partition, SQUEEZE_COMMIT_V3.order)
    storage_preflight(client, tables=(shape,))
    child_count = client.execute(
        "SELECT count() FROM system.tables WHERE database='arte' "
        "AND name='trading_portfolio_reservation_reason_v1'"
    ).strip()
    if child_count not in {"0", "1"}:
        raise RuntimeError("Reservation reason table catalog is ambiguous")
    child_present = child_count == "1"
    if child_present:
        storage_preflight(client, tables=(RESERVATION_REASON,))
    if actual_core == core_full and child_present:
        print("V3 reservation reasons: schema and SSD placement verified; no change")
        return "verified"
    fence_rows = client.execute("SELECT count() FROM arte.trading_commit_v3").strip()
    if fence_rows != "0":
        raise RuntimeError("V3 commit has rows; versioned migration required")
    if child_present and client.execute(
            "SELECT count() FROM arte.trading_portfolio_reservation_reason_v1"
    ).strip() != "0":
        raise RuntimeError("Reservation reason child has rows; no ALTER attempted")
    pending = ["child table"] if not child_present else []
    if actual_core == old:
        pending.append("V3 reason count and hash")
    elif actual_core == partial:
        pending.append("V3 reason hash")
    print("V3 reservation reasons: empty fence verified; pending " + ", ".join(pending))
    if not apply:
        print("Plan only; no ClickHouse state changed")
        return "planned"
    child_ddl, count_ddl, hash_ddl = staged_reservation_reason_ddl()
    if not child_present:
        client.execute(child_ddl)
        storage_preflight(client, tables=(RESERVATION_REASON,))
    if actual_core == old:
        client.execute(count_ddl)
    if actual_core in {old, partial}:
        client.execute(hash_ddl)
    final_names = {name for name, _ in actual} | _REASON_COLUMNS
    final_shape = TableContract(SQUEEZE_COMMIT_V3.name,
                                tuple(column for column in full if column[0] in final_names),
                                SQUEEZE_COMMIT_V3.partition, SQUEEZE_COMMIT_V3.order)
    storage_preflight(client, tables=(RESERVATION_REASON, final_shape))
    print("V3 reservation reasons: table and commit fence verified; 0 rows inserted")
    return "upgraded"


def install_missing(client: object, *, apply: bool, profile: str = "fixed-v2",
                    verify_batch_size: int = 8) -> tuple[int, int]:
    """Return (already installed, newly created); verify bounded DDL batches."""
    if type(verify_batch_size) is not int or not 1 <= verify_batch_size <= 16:
        raise ValueError("Journal verification batch size must be 1–16")
    policies = [json.loads(line) for line in client.execute(
        "SELECT disks FROM system.storage_policies "
        f"WHERE policy_name='{STORAGE_POLICY}' FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    if len(policies) != 1 or policies[0].get("disks") != [STORAGE_POLICY]:
        raise RuntimeError("Journal install requires SSD-only live_market_ssd")
    if client.execute("SELECT count() FROM system.databases WHERE name='arte'").strip() != "1":
        raise RuntimeError("Existing arte database is required")
    contracts = profile_contracts(profile)
    by_name = {table.name: table for table in contracts}
    missing, _ = (plan_missing(client) if profile == "fixed-v2" else
                  plan_missing(client, profile=profile))
    installed = len(contracts) - len(missing)
    print(f"Normalized journal: {installed} verified, {len(missing)} absent")
    if not apply:
        print("Plan only; no ClickHouse state changed")
        return installed, 0
    created = 0
    pending = []
    for name in missing:
        # IF NOT EXISTS protects restart after a lost response. The exact
        # batch check catches an incompatible table; it is never overwritten.
        client.execute(by_name[name].ddl())
        pending.append(by_name[name])
        if len(pending) == verify_batch_size or name == missing[-1]:
            storage_preflight(client, tables=tuple(pending))
            created += len(pending)
            print(f"Created and verified {created}/{len(missing)}; "
                  f"latest arte.{name}", flush=True)
            pending.clear()
    storage_preflight(client, tables=contracts)
    print(f"Complete: {len(contracts)} verified; {created} newly created; 0 rows inserted")
    return installed, created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://DESKTOP-SAAI85T:18123")
    parser.add_argument("--apply", action="store_true",
                        help="apply requested table DDL after exact preflight")
    parser.add_argument("--profile", choices=("fixed-v2", "fixed-v3"),
                        default="fixed-v2", help="exact journal table layout")
    parser.add_argument("--upgrade-v3-reservation-reason", action="store_true",
                        help="verify or install the empty-fence V3 reason upgrade")
    parser.add_argument("--upgrade-v3-reconciliation-difference", action="store_true",
                        help="verify or install the empty-fence V3 reconciliation child upgrade")
    parser.add_argument("--upgrade-v3-portfolio-control", action="store_true",
                        help="verify or install the empty-fence V3 scalar control upgrade")
    parser.add_argument("--upgrade-v3-trade-proposal", action="store_true",
                        help="verify or install the empty-fence V3 proposal children")
    parser.add_argument("--upgrade-v3-broker-oms", action="store_true",
                        help="verify or install empty-fence V3 broker/OMS children")
    parser.add_argument("--upgrade-v3-entry-reprice-capacity", action="store_true",
                        help="verify or install empty-fence V3 capacity children")
    parser.add_argument("--upgrade-v3-entry-reprice-rejected", action="store_true",
                        help="verify or install empty-fence V3 reprice refusal fact")
    parser.add_argument("--upgrade-v3-protected-exit-satisfied", action="store_true",
                        help="verify or install empty-fence V3 protected-exit fact")
    parser.add_argument("--install-live-plan-membership", action="store_true",
                        help="verify or install typed live plan membership tables")
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if (platform.node().upper() != "DESKTOP-SAAI85T"
            or (parsed.scheme, parsed.hostname, parsed.port, parsed.path,
                parsed.query, parsed.fragment, parsed.username, parsed.password)
            != ("http", "desktop-saai85t", 18123, "", "", "", None, None)):
        parser.error("Run on DESKTOP-SAAI85T against its managed ClickHouse endpoint")
    try:
        addresses = {result[4][0] for result in socket.getaddrinfo(
            parsed.hostname, parsed.port, family=socket.AF_INET,
            type=socket.SOCK_STREAM)}
        if WORKSTATION_IPV4 not in addresses:
            raise RuntimeError("Pinned workstation IPv4 is not in hostname resolution")
        client = _admin_client(f"http://{WORKSTATION_IPV4}:{parsed.port}")
        try:
            if sum((args.upgrade_v3_reservation_reason,
                    args.upgrade_v3_reconciliation_difference,
                    args.upgrade_v3_portfolio_control,
                    args.upgrade_v3_trade_proposal,
                    args.upgrade_v3_broker_oms,
                    args.upgrade_v3_entry_reprice_capacity,
                    args.upgrade_v3_entry_reprice_rejected,
                    args.upgrade_v3_protected_exit_satisfied,
                    args.install_live_plan_membership)) > 1:
                parser.error("Select only one layout upgrade at a time")
            if args.install_live_plan_membership:
                result = install_live_plan_membership(client, apply=args.apply)
                print(f"Live plan membership layout: {result}; no rows inserted")
            elif args.upgrade_v3_protected_exit_satisfied:
                result = upgrade_v3_protected_exit_satisfied(client, apply=args.apply)
                print(f"V3 protected-exit layout: {result}; no rows inserted")
            elif args.upgrade_v3_entry_reprice_rejected:
                result = upgrade_v3_entry_reprice_rejected(client, apply=args.apply)
                print(f"V3 entry-reprice-refusal layout: {result}; no rows inserted")
            elif args.upgrade_v3_entry_reprice_capacity:
                result = upgrade_v3_entry_reprice_capacity(client, apply=args.apply)
                print(f"V3 entry-reprice-capacity layout: {result}; no rows inserted")
            elif args.upgrade_v3_broker_oms:
                result = upgrade_v3_broker_oms(client, apply=args.apply)
                print(f"V3 broker/OMS layout: {result}; no rows inserted")
            elif args.upgrade_v3_trade_proposal:
                result = upgrade_v3_trade_proposal(client, apply=args.apply)
                print(f"V3 trade-proposal layout: {result}; no rows inserted")
            elif args.upgrade_v3_portfolio_control:
                result = upgrade_v3_portfolio_control(client, apply=args.apply)
                print(f"V3 portfolio-control layout: {result}; no rows inserted")
            elif args.upgrade_v3_reconciliation_difference:
                result = upgrade_v3_reconciliation_difference(client, apply=args.apply)
                print(f"V3 reconciliation-difference layout: {result}; no rows inserted")
            elif args.upgrade_v3_reservation_reason:
                result = upgrade_v3_reservation_reason(client, apply=args.apply)
                print(f"V3 reservation-reason layout: {result}; no rows inserted")
            else:
                install_missing(client, apply=args.apply, profile=args.profile)
        finally:
            client.close()
    except KeyboardInterrupt:
        print("Interrupted; completed DDL remains. Rerun to verify or finish.",
              file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Journal layout blocked: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
