"""Plan or explicitly install the dedicated fixed-lot runner; default is read-free."""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import platform
import socket
import secrets
from hashlib import sha256

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.clickhouse.provision_backtest_v4_runner import desired_plan as legacy_plan
from scripts.clickhouse.provision_fixed_backtest_v3_principals import (
    URL, WORKSTATION_IPV4, _desired_grants, _effective_grants)
from scripts.clickhouse.provision_trading_journal import (
    SECRET_ROOT, _admin_client, _restrict_secret_file)
from src.trading_runtime.arte_journal_writer import v4_storage_contracts
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
from src.trading_runtime.fixed_structural_lot_entry_schema import TABLES as ENTRY_TABLES
from src.trading_runtime.fixed_structural_lot_snapshot import TABLES as SNAPSHOT_TABLES
from src.trading_runtime.fixed_structural_lot_manager_schema import TABLES as MANAGER_TABLES

PRINCIPAL = 'backtest_v4_fixed_structural_lot_runner'
STEM = 'BACKTEST_V4_FIXED_STRUCTURAL_LOT_RUNNER_CLICKHOUSE_'
SECRET_PATH = SECRET_ROOT / (PRINCIPAL + '.env')


def _tables(policy):
    if type(policy) is not FixedStructuralLotPolicy:
        raise ValueError('Exact typed fixed-lot declaration required')
    policy.__post_init__()
    return (*ENTRY_TABLES, *SNAPSHOT_TABLES, *MANAGER_TABLES)


def desired_plan(policy):
    names = frozenset(table.name for table in _tables(policy))
    base = legacy_plan()
    return replace(base, principal=PRINCIPAL,
                   select_arte=base.select_arte | names,
                   insert_arte=base.insert_arte | names)


def table_install_plan(policy):
    return tuple(table.ddl() for table in _tables(policy))


def validate_existing_storage(client, policy):
    """SELECT-only exact schema/policy/actual-parts check; never repairs drift."""
    storage_preflight(client, tables=_tables(policy))


def credential(*, account_exists):
    if type(account_exists) is not bool:
        raise ValueError('Exact principal inventory required')
    if SECRET_PATH.exists():
        _restrict_secret_file(SECRET_PATH)
        lines = SECRET_PATH.read_text(encoding='utf-8').splitlines()
        pairs = [line.split('=', 1) for line in lines if '=' in line]
        values = dict(pairs)
        if (len(lines) != 3 or len(pairs) != 3 or len(values) != 3
                or set(values) != {STEM + key for key in ('URL', 'USER', 'PASSWORD')}
                or values[STEM + 'URL'] != URL or values[STEM + 'USER'] != PRINCIPAL
                or len(values[STEM + 'PASSWORD']) < 40):
            raise RuntimeError('Fixed-lot private credential has another exact profile')
        return values[STEM + 'PASSWORD']
    if account_exists:
        raise RuntimeError('Existing fixed-lot principal lacks its private credential')
    with SECRET_PATH.open('x', encoding='utf-8'):
        pass
    _restrict_secret_file(SECRET_PATH)
    password = secrets.token_urlsafe(48)
    SECRET_PATH.write_text(f'{STEM}URL={URL}\n{STEM}USER={PRINCIPAL}\n'
                           f'{STEM}PASSWORD={password}\n', encoding='utf-8')
    _restrict_secret_file(SECRET_PATH)
    return password


def _writer_inventory(writer, plan):
    if writer.execute('SELECT currentUser()').strip() != PRINCIPAL:
        raise RuntimeError('Fixed-lot credential authenticates as another principal')
    if writer.execute("SELECT getSetting('readonly')").strip() != '0':
        raise RuntimeError('Fixed-lot append runner requires readonly=0')
    return _effective_grants(writer, plan)


def apply_with_clients(*, admin, credential, client_factory, policy, install_tables=False):
    """Operator-only DDL/grants; this issues no installed execution capability."""
    tables = _tables(policy)
    if type(install_tables) is not bool:
        raise ValueError('Table installation must be explicit')
    plan = desired_plan(policy)
    if admin.execute('SELECT currentUser()').strip() == PRINCIPAL:
        raise RuntimeError('Fixed-lot installation requires a distinct operator')
    present = admin.execute(f"SELECT count() FROM system.users WHERE name='{PRINCIPAL}' FORMAT TabSeparated").strip()
    if present not in {'0', '1'}:
        raise RuntimeError('Fixed-lot principal inventory is ambiguous')
    disks = [json.loads(line) for line in admin.execute(
        "SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd' FORMAT JSONEachRow").splitlines() if line.strip()]
    if disks != [{'disks': ['live_market_ssd']}]:
        raise RuntimeError('Fixed-lot installation requires SSD-only live_market_ssd')
    names = ','.join("'" + table.name + "'" for table in tables)
    found = [json.loads(line)['name'] for line in admin.execute(
        f"SELECT name FROM system.tables WHERE database='arte' AND name IN ({names}) FORMAT JSONEachRow").splitlines() if line.strip()]
    if len(set(found)) != len(found) or not set(found) <= {t.name for t in tables}:
        raise RuntimeError('Fixed-lot table inventory is ambiguous')
    missing = tuple(t for t in tables if t.name not in found)
    storage_preflight(admin, tables=(*v4_storage_contracts(), *(t for t in tables if t.name in found)))
    if missing and not install_tables:
        raise RuntimeError('Selected tables absent; explicit table installation required')
    password = credential(account_exists=present == '1')
    if type(password) is not str or len(password) < 40:
        raise RuntimeError('Fixed-lot runner requires its complete private credential')
    writer = None
    try:
        if present == '1':
            writer = client_factory(PRINCIPAL, password)
            _writer_inventory(writer, plan)  # No DDL/grant on broad existing authority.
        for table in missing:
            admin.execute(table.ddl())
        validate_existing_storage(admin, policy)
        if present == '0':
            digest = sha256(password.encode()).hexdigest()
            admin.execute(f"CREATE USER {PRINCIPAL} IDENTIFIED WITH sha256_hash BY '{digest}' "
                          "HOST IP '172.16.0.0/12', IP '127.0.0.1', IP '::1'")
            writer = client_factory(PRINCIPAL, password)
        have = _writer_inventory(writer, plan)
        wanted = _desired_grants(plan)
        for privilege, database, table in sorted(wanted - have):
            admin.execute(f'GRANT {privilege} ON {database}.{table} TO {PRINCIPAL}')
        if _writer_inventory(writer, plan) != wanted:
            raise RuntimeError('Fixed-lot installed grants differ from the exact plan')
        storage_preflight(writer, tables=(*v4_storage_contracts(), *tables))
    finally:
        if writer is not None:
            writer.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=3)
    parser.add_argument('--json', action='store_true', help='Emit the complete machine-readable plan')
    parser.add_argument('--apply', action='store_true', help='Apply only on the managed workstation')
    parser.add_argument('--confirm-fixed-lot-runner', action='store_true')
    parser.add_argument('--install-tables', action='store_true', help='Create only absent selected tables')
    parser.add_argument('--confirm-install-tables', action='store_true')
    args = parser.parse_args(argv)
    policy = FixedStructuralLotPolicy(count=args.count)
    plan = desired_plan(policy)
    if args.apply and not args.confirm_fixed_lot_runner:
        parser.error('--apply requires --confirm-fixed-lot-runner')
    if args.install_tables and (not args.apply or not args.confirm_install_tables):
        parser.error('--install-tables requires --apply and --confirm-install-tables')
    if args.confirm_install_tables and not args.install_tables:
        parser.error('--confirm-install-tables requires --install-tables')
    if args.apply and args.json:
        parser.error('--json is plan-only')
    if args.apply:
        admin = None
        try:
            if platform.node().upper() != 'DESKTOP-SAAI85T' or not SECRET_ROOT.is_dir():
                raise RuntimeError('Managed workstation secret root required')
            addresses = {row[4][0] for row in socket.getaddrinfo('desktop-saai85t',18123,
                family=socket.AF_INET,type=socket.SOCK_STREAM)}
            if WORKSTATION_IPV4 not in addresses:
                raise RuntimeError('Pinned workstation transport outside hostname resolution')
            from research.mlops.clickhouse import ClickHouseHttpClient
            transport = f'http://{WORKSTATION_IPV4}:18123'
            admin = _admin_client(transport)
            print('Active: verify storage, selected tables, private account and exact grants')
            apply_with_clients(admin=admin, credential=credential, policy=policy,
                install_tables=args.install_tables, client_factory=lambda user,password:
                    ClickHouseHttpClient(transport,user,password,timeout_seconds=60))
        except KeyboardInterrupt:
            print('Interrupted; reconcile the same exact plan before resuming',file=sys.stderr)
            return 130
        except Exception as exc:
            print(f'Fixed-lot installation stopped: {type(exc).__name__}; '
                  'state may be partial; inspect privately before resuming',file=sys.stderr)
            return 1
        finally:
            if admin is not None:
                try:
                    admin.close()
                except Exception as exc:
                    print(f'Fixed-lot operator close failed: {type(exc).__name__}',file=sys.stderr)
                    return 1
        print('Verified: seven selected tables, SSD parts and exact dedicated grants; '
              '0 data rows inserted. Installed execution/source preflight remains required.')
        return 0
    payload = {'status': 'plan_only', 'principal': PRINCIPAL,
               'storage_policy': 'live_market_ssd',
               'select_arte': sorted(plan.select_arte), 'insert_arte': sorted(plan.insert_arte),
               'tables': [table.name for table in _tables(policy)],
               'ddl': list(table_install_plan(policy)), 'operations_executed': 0}
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f'Plan only: {PRINCIPAL}; 7 selected tables on live_market_ssd.')
        print('No database, credential, or grant operations executed. Installation requires separate approval.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
