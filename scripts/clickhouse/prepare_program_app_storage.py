"""Plan or audit central programme storage; create missing tables only with approval.

This operator command installs no strategy, grants no access, publishes no rows,
and starts no producer or backtest. Existing incompatible tables require their
own explicit migration; this command never alters or relocates them.
"""
import argparse
import os
from pathlib import Path
import platform
import sys

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.backend.program_app_storage import program_app_storage_contracts
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.arte_journal_writer import _rows, _literal


def inspect_inventory(client, contracts):
    """Read placement before any DDL; stop on incompatible existing storage."""
    policies = _rows(client, "SELECT disks FROM system.storage_policies "
        "WHERE policy_name='live_market_ssd' FORMAT JSONEachRow")
    if len(policies) != 1 or policies[0].get('disks') != ['live_market_ssd']:
        raise ValueError('Programme app requires the existing SSD-only live_market_ssd policy')
    names = ','.join(_literal(table.name) for table in contracts)
    rows = _rows(client, "SELECT name FROM system.tables WHERE database='arte' "
        f"AND name IN ({names}) FORMAT JSONEachRow")
    present = {row['name'] for row in rows}
    if len(present) != len(rows) or present - {table.name for table in contracts}:
        raise ValueError('Programme app storage inventory is duplicate or outside its scope')
    existing = tuple(table for table in contracts if table.name in present)
    if existing:
        # Includes keys, columns, indexes, table policy and actual active parts.
        storage_preflight(client, tables=existing)
    return existing, tuple(table for table in contracts if table.name not in present)


def prepare_with_client(client, *, apply, emit=print):
    contracts = program_app_storage_contracts()
    existing, missing = inspect_inventory(client, contracts)
    emit(f'Existing contracts verified: {len(existing)} | Missing: {len(missing)} | Rows inserted: 0')
    if not apply:
        emit('AUDIT ONLY | Storage creation requires separate operator approval.')
        for table in missing: emit('MISSING ' + table.name)
        return len(missing)
    for index, table in enumerate(missing, 1):
        emit(f'Creating missing contract {index}/{len(missing)}: {table.name}')
        client.execute(table.ddl())
        # Only acknowledge a created contract after complete physical audit.
        storage_preflight(client, tables=(table,))
    storage_preflight(client, tables=contracts)
    emit(f'STORAGE VERIFIED | {len(contracts)} contracts | 0 rows inserted | No strategy registered')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--audit', action='store_true', help='SELECT-only workstation inventory and placement audit')
    actions.add_argument('--apply', action='store_true', help='Create missing central contracts after approval')
    parser.add_argument('--confirm-program-app-storage', action='store_true')
    parser.add_argument('--show-ddl', action='store_true', help='Print declared DDL in plan-only mode')
    args = parser.parse_args(argv)
    if args.apply and not args.confirm_program_app_storage:
        parser.error('--apply requires --confirm-program-app-storage and prior operator approval')
    contracts = program_app_storage_contracts()
    if not args.audit and not args.apply:
        print(f'PLAN ONLY | {len(contracts)} central contracts | Workload policy: live_market_ssd')
        print('No connection opened; no credentials read; no storage or rows changed.')
        print('Next: run --audit on the workstation; obtain approval before --apply.')
        if args.show_ddl:
            for table in contracts: print(table.ddl() + ';')
        return 0
    if args.show_ddl:
        parser.error('--show-ddl is available only in plan-only mode')
    from scripts.clickhouse.provision_trading_journal import SECRET_ROOT, _admin_client
    client = None
    try:
        if platform.node().upper() != 'DESKTOP-SAAI85T' or not SECRET_ROOT.is_dir():
            print('STORAGE BLOCKED | Run on DESKTOP-SAAI85T with its managed secret root.', file=sys.stderr)
            return 1
        client = _admin_client('http://DESKTOP-SAAI85T:18123')
        if args.audit:
            client.default_query_params['readonly'] = '1'
            settings = _rows(client, "SELECT value FROM system.settings WHERE name='readonly' FORMAT JSONEachRow")
            if len(settings) != 1 or str(settings[0].get('value')) != '1':
                raise ValueError('Read-only audit setting was not established')
        prepare_with_client(client, apply=args.apply)
        return 0
    except KeyboardInterrupt:
        print('INTERRUPTED | Rerun the audit; verified existing contracts are preserved.', file=sys.stderr)
        return 130
    except Exception as error:
        # Transport exceptions can contain private authentication details.
        print(f'STORAGE BLOCKED | {type(error).__name__}; inspect privately before retrying.', file=sys.stderr)
        return 1
    finally:
        if client is not None: client.close()


if __name__ == '__main__':
    raise SystemExit(main())
