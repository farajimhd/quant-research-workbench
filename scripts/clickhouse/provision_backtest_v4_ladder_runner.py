"""Provision the explicit native ladder journal principal; plan-only by default.

No existing runner grants are changed. Credentials remain in the workstation
secret root. This principal receives normalized journal INSERT and source
SELECT only, never DDL or market writes.
"""
import argparse
from dataclasses import replace
from hashlib import sha256
import os
from pathlib import Path
import platform
import secrets
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True

from scripts.clickhouse.provision_backtest_v4_runner import desired_plan as base_plan
from scripts.clickhouse.provision_fixed_backtest_v3_principals import (
    URL, WORKSTATION_IPV4, _desired_grants, _effective_grants)
from scripts.clickhouse.provision_trading_journal import (
    SECRET_ROOT, _admin_client, _restrict_secret_file)
from src.trading_runtime.arte_journal_writer import _v4_preflight, v4_storage_contracts
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.arte_squeeze_ladder_schema import TABLES

PRINCIPAL = 'backtest_v4_ladder_runner'
SECRET_PATH = SECRET_ROOT / (PRINCIPAL + '.env')
STEM = 'BACKTEST_V4_LADDER_RUNNER_CLICKHOUSE_'


def desired_plan():
    base = base_plan()
    tables = frozenset(table.name for table in TABLES)
    return replace(base, principal=PRINCIPAL,
        select_arte=base.select_arte | tables, insert_arte=base.insert_arte | tables)


def credential(*, account_exists):
    if SECRET_PATH.exists():
        _restrict_secret_file(SECRET_PATH)
        values = dict(line.split('=', 1) for line in SECRET_PATH.read_text().splitlines() if '=' in line)
        if (set(values) != {STEM + suffix for suffix in ('URL', 'USER', 'PASSWORD')}
                or values[STEM + 'URL'] != URL or values[STEM + 'USER'] != PRINCIPAL
                or len(values[STEM + 'PASSWORD']) < 40):
            raise RuntimeError('Ladder private credential has a different exact profile')
        return values[STEM + 'PASSWORD']
    if account_exists:
        raise RuntimeError('Existing ladder principal has no private credential')
    with SECRET_PATH.open('x'):
        pass
    _restrict_secret_file(SECRET_PATH)
    password = secrets.token_urlsafe(48)
    SECRET_PATH.write_text(f'{STEM}URL={URL}\n{STEM}USER={PRINCIPAL}\n{STEM}PASSWORD={password}\n')
    _restrict_secret_file(SECRET_PATH)
    return password


def apply_with_clients(*, admin, credential, client_factory):
    plan = desired_plan()
    if admin.execute('SELECT currentUser()').strip() == PRINCIPAL:
        raise RuntimeError('Ladder provisioning requires a distinct administrator')
    present = admin.execute(f"SELECT count() FROM system.users WHERE name='{PRINCIPAL}' FORMAT TabSeparated").strip()
    if present not in {'0', '1'}:
        raise RuntimeError('Ladder principal inventory is ambiguous')
    storage_preflight(admin, tables=(*v4_storage_contracts(), *TABLES))
    password = credential(account_exists=present == '1')
    if not isinstance(password, str) or len(password) < 40:
        raise RuntimeError('Ladder principal requires its private complete credential')
    if present == '0':
        digest = sha256(password.encode()).hexdigest()
        admin.execute(f"CREATE USER {PRINCIPAL} IDENTIFIED WITH sha256_hash BY '{digest}' "
            "HOST IP '172.16.0.0/12', IP '127.0.0.1', IP '::1'")
    writer = client_factory(PRINCIPAL, password)
    try:
        if writer.execute('SELECT currentUser()').strip() != PRINCIPAL:
            raise RuntimeError('Ladder saved credential authenticates as another principal')
        have = _effective_grants(writer, plan) if present == '1' else frozenset()
        for privilege, database, table in sorted(_desired_grants(plan) - have):
            admin.execute(f'GRANT {privilege} ON {database}.{table} TO {PRINCIPAL}')
        writer.automatic_ladder_profile = True
        _v4_preflight(writer)
    finally:
        writer.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm-ladder-runner', action='store_true')
    args = parser.parse_args(argv)
    plan = desired_plan()
    print(f'Ladder runner: {plan.principal}; SELECT {len(plan.select_arte)}; INSERT {len(plan.insert_arte)}')
    if not args.apply:
        print('Plan only; no connection, credentials, grants or rows changed')
        return 0
    if not args.confirm_ladder_runner:
        parser.error('--apply requires --confirm-ladder-runner')
    if platform.node().upper() != 'DESKTOP-SAAI85T' or not SECRET_ROOT.is_dir():
        raise RuntimeError('Ladder provisioning requires the managed workstation secret root')
    addresses = {row[4][0] for row in socket.getaddrinfo('desktop-saai85t', 18123,
        family=socket.AF_INET, type=socket.SOCK_STREAM)}
    if WORKSTATION_IPV4 not in addresses:
        raise RuntimeError('Pinned workstation transport is outside hostname resolution')
    from research.mlops.clickhouse import ClickHouseHttpClient
    transport = f'http://{WORKSTATION_IPV4}:18123'
    admin = _admin_client(transport)
    try:
        apply_with_clients(admin=admin, credential=credential,
            client_factory=lambda user, password: ClickHouseHttpClient(
                transport, user, password, timeout_seconds=60))
    except Exception as exc:
        # Underlying HTTP messages may contain the credential hash; never print.
        print(f'Ladder provisioning stopped: {type(exc).__name__}; rerun after private diagnosis', file=sys.stderr)
        return 1
    finally:
        admin.close()
    print('Ladder exact grants and SSD placement verified; 0 rows inserted')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
