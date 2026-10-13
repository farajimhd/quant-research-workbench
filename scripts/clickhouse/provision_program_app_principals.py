"""Provision only an explicitly approved shared programme credential plan.

Plan-only by default. Creates no tables or rows and starts no publication,
producer, service or backtest. Private credentials stay on the workstation.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import re
import secrets
import socket
import sys

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.clickhouse.plan_program_app_principals import desired_plans, HISTORY, OPENING, LIQUIDITY, RELEASE, SELECTION
from scripts.clickhouse.provision_fixed_backtest_v3_principals import URL, WORKSTATION_IPV4
from scripts.clickhouse.provision_trading_journal import SECRET_ROOT, _admin_client, _restrict_secret_file
from src.backend.managed_backtest_credentials import _private_values
from src.backend.strategy_configuration_reader import _unique_object, _reject_constant
from src.trading_runtime.arte_journal_schema import storage_preflight


def approved_plan(path, plans):
    selected = Path(path).resolve()
    if not selected.is_relative_to(Path('D:/TradingML/runtimes').resolve()) or selected.stat().st_size > 256 * 1024:
        raise ValueError('Approval plan must be a bounded workstation runtime artifact')
    payload = json.loads(selected.read_text(encoding='utf-8-sig'),
        object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    expected = [{'principal': p.principal, 'select_tables': [list(v) for v in sorted(p.reads)],
        'insert_tables': [list(v) for v in sorted(p.writes)], 'grants': list(p.grants())} for p in plans]
    if type(payload) is not dict or payload.get('principals') != expected:
        raise ValueError('Approved principals or exact grants differ from current source')
    return plans


def credential(plan, *, account_exists):
    path = SECRET_ROOT / (plan.principal + '.env')
    stem = plan.principal.upper() + '_CLICKHOUSE_'
    allowed = tuple(stem + suffix for suffix in ('URL', 'USER', 'PASSWORD'))
    if path.exists():
        _restrict_secret_file(path)
        values = _private_values(path, allowed)
        if (values[stem + 'URL'] != URL or values[stem + 'USER'] != plan.principal
                or len(values[stem + 'PASSWORD']) < 40):
            raise ValueError('Private programme credential differs from its exact profile')
        return values[stem + 'PASSWORD']
    if account_exists:
        raise ValueError('Existing programme account has no private credential')
    with path.open('x'):
        pass
    _restrict_secret_file(path)
    password = secrets.token_urlsafe(48)
    path.write_text(f'{stem}URL={URL}\n{stem}USER={plan.principal}\n{stem}PASSWORD={password}\n', encoding='utf-8')
    _restrict_secret_file(path)
    return password


def effective_grants(client, plan):
    allowed = {('SELECT', *scope) for scope in plan.reads} | {('INSERT', *scope) for scope in plan.writes}
    actual = set()
    for line in client.execute('SHOW GRANTS FINAL').splitlines():
        match = re.fullmatch(r'GRANT ([A-Z ,]+) ON ([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*) TO ([a-z][a-z0-9_]*)', line.strip())
        if match is None or match[4] != plan.principal:
            raise ValueError('Programme account has broad, delegated or unknown grants')
        for privilege in match[1].split(','):
            scope = (privilege.strip(), match[2], match[3])
            if scope not in allowed:
                raise ValueError('Programme account has authority outside approved scope')
            actual.add(scope)
    return frozenset(actual)


def apply_with_clients(*, admin, plans, credential_factory, client_factory, emit=print, storage_audit=None):
    """Audit every existing account before creating or granting any account."""
    if admin.execute('SELECT currentUser()').strip() in {p.principal for p in plans}:
        raise ValueError('Provisioning requires a distinct operator authority')
    if storage_audit is not None:
        if not callable(storage_audit):
            raise ValueError('Principal provisioning requires its explicit storage auditor')
        storage_audit(admin)
    else:
        for database in ('arte', 'q_live'):
            contracts = tuple({(t.database, t.name): t for t in
                (*HISTORY, *OPENING, *LIQUIDITY, *RELEASE, *SELECTION) if t.database == database}.values())
            storage_preflight(admin, tables=contracts)
    present = {}
    for p in plans:
        count = admin.execute(f"SELECT count() FROM system.users WHERE name='{p.principal}' FORMAT TabSeparated").strip()
        if count not in {'0', '1'}:
            raise ValueError('Programme account inventory is ambiguous')
        present[p.principal] = count == '1'
    clients, passwords = {}, {}
    failure = None
    try:
        for p in plans:
            if present[p.principal]:
                password = credential_factory(p, account_exists=True)
                client = client_factory(p.principal, password, readonly=not p.writes)
                clients[p.principal] = client
                if client.execute('SELECT currentUser()').strip() != p.principal:
                    raise ValueError('Saved private credential authenticates as another account')
                effective_grants(client, p)
                passwords[p.principal] = password
        for p in plans:
            if not present[p.principal]:
                passwords[p.principal] = credential_factory(p, account_exists=False)
        if (any(type(v) is not str or len(v) < 40 for v in passwords.values())
                or len(set(passwords.values())) != len(plans)):
            raise ValueError('Programme accounts require distinct complete private credentials')
        for index, p in enumerate(plans, 1):
            if not present[p.principal]:
                digest = sha256(passwords[p.principal].encode()).hexdigest()
                admin.execute(f"CREATE USER {p.principal} IDENTIFIED WITH sha256_hash BY '{digest}' "
                    "HOST IP '172.16.0.0/12', IP '127.0.0.1', IP '::1'")
                clients[p.principal] = client_factory(p.principal, passwords[p.principal], readonly=not p.writes)
            client = clients[p.principal]
            if client.execute('SELECT currentUser()').strip() != p.principal:
                raise ValueError('Programme credential authenticates as another account')
            expected = {('SELECT', *s) for s in p.reads} | {('INSERT', *s) for s in p.writes}
            for privilege, database, table in sorted(expected - effective_grants(client, p)):
                admin.execute(f'GRANT {privilege} ON {database}.{table} TO {p.principal}')
            if effective_grants(client, p) != expected:
                raise ValueError('Programme effective grants differ after provisioning')
            if not p.writes and client.execute("SELECT getSetting('readonly')").strip() != '1':
                raise ValueError('Programme reader transport is not SELECT-only')
            emit(f'VERIFIED {index}/{len(plans)} | {p.principal} | SELECT {len(p.reads)} | INSERT {len(p.writes)}')
    except BaseException as error:
        failure = error
        raise
    finally:
        cleanup = []
        for client in clients.values():
            try:
                client.close()
            except BaseException as error:
                cleanup.append(error)
        if cleanup:
            raise BaseExceptionGroup('Programme credential client cleanup failed',
                ([failure] if failure is not None else []) + cleanup)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm-program-principals', action='store_true')
    parser.add_argument('--approved-plan')
    args = parser.parse_args(argv)
    plans = desired_plans()
    if not args.apply:
        if args.confirm_program_principals or args.approved_plan:
            parser.error('Confirmation and approval plan are available only with --apply')
        print(f'PLAN ONLY | {len(plans)} isolated accounts | No connections, credentials or changes')
        return 0
    if not args.confirm_program_principals or not args.approved_plan:
        parser.error('--apply requires --confirm-program-principals and --approved-plan')
    admin = None
    try:
        approved_plan(args.approved_plan, plans)
        if platform.node().upper() != 'DESKTOP-SAAI85T' or not SECRET_ROOT.is_dir():
            raise ValueError('Programme provisioning requires the managed workstation secret root')
        addresses = {row[4][0] for row in socket.getaddrinfo('desktop-saai85t', 18123,
            family=socket.AF_INET, type=socket.SOCK_STREAM)}
        if WORKSTATION_IPV4 not in addresses:
            raise ValueError('Pinned workstation transport is outside hostname resolution')
        from research.mlops.clickhouse import ClickHouseHttpClient
        transport = f'http://{WORKSTATION_IPV4}:18123'
        def client_factory(user, password, *, readonly):
            return ClickHouseHttpClient(transport, user, password, timeout_seconds=60,
                default_query_params={'readonly': '1'} if readonly else None)
        admin = _admin_client(transport)
        apply_with_clients(admin=admin, plans=plans, credential_factory=credential,
            client_factory=client_factory, emit=lambda value: print(value, flush=True))
    except KeyboardInterrupt:
        print('INTERRUPTED | Audit the same approved plan before resuming', file=sys.stderr)
        return 130
    except BaseExceptionGroup:
        print('PROVISIONING BLOCKED | Client cleanup failed; diagnose privately before retrying', file=sys.stderr)
        return 1
    except Exception as error:
        print(f'PROVISIONING BLOCKED | {type(error).__name__}; diagnose privately before retrying', file=sys.stderr)
        return 1
    finally:
        if admin is not None:
            try:
                admin.close()
            except Exception as error:
                print(f'PROVISIONING BLOCKED | Operator cleanup {type(error).__name__}; diagnose privately', file=sys.stderr)
                return 1
    print('COMPLETE | Exact grants verified | 0 rows inserted | No producers or runs started')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
