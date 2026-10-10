"""Plan or provision the separately declared original-risk and entry-cost diagnostic capability.

Default invocation opens no connection or credential. Apply requires explicit
policy confirmation on the managed workstation. Optional diagnostic-table DDL is
operator-only; the runner receives exact SELECT/INSERT grants and never DDL.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import secrets
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True

from scripts.clickhouse.provision_backtest_v4_entry_cost_runner import desired_plan as legacy_plan
from scripts.clickhouse.provision_fixed_backtest_v3_principals import (
    URL, WORKSTATION_IPV4, _desired_grants, _effective_grants)
from scripts.clickhouse.provision_trading_journal import (
    SECRET_ROOT, _admin_client, _restrict_secret_file)
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.arte_journal_writer import _v4_preflight, v4_storage_contracts
from src.trading_runtime.arte_original_risk_diagnostic_v4 import DIAGNOSTIC
from src.trading_runtime.original_risk_pending_snapshot import selected_snapshot_contracts
from src.trading_runtime.consecutive_price_confirmed_risk import ConsecutivePriceRiskPolicy, ENTRY_COST_CAPABILITY_RULE
from src.trading_runtime.arte_entry_spread_risk_v4 import ENTRY_SPREAD_RISK

PRINCIPAL = 'backtest_v4_original_risk_entry_cost_runner'
STEM = 'BACKTEST_V4_ORIGINAL_RISK_ENTRY_COST_RUNNER_CLICKHOUSE_'
SECRET_PATH = SECRET_ROOT / (PRINCIPAL + '.env')


def _policy(policy):
    if type(policy) is not ConsecutivePriceRiskPolicy or not policy.entry_cost_capability:
        raise ValueError('Combined original-risk and entry-cost provisioning requires its exact typed original-risk policy')
    policy.__post_init__()
    return policy


def desired_plan(policy):
    _policy(policy)
    base = legacy_plan()
    return replace(base, principal=PRINCIPAL,
                   select_arte=base.select_arte | frozenset(t.name for t in (DIAGNOSTIC,*selected_snapshot_contracts())),
                   insert_arte=base.insert_arte | frozenset(t.name for t in (DIAGNOSTIC,*selected_snapshot_contracts())))


def diagnostic_install_plan(policy):
    """Exact operator DDL; never repairs a drifted existing table implicitly."""
    _policy(policy)
    return tuple(t.ddl() for t in (DIAGNOSTIC,*selected_snapshot_contracts()))


def credential(*, account_exists):
    if type(account_exists) is not bool:
        raise ValueError('Combined original-risk and entry-cost principal inventory must be exact')
    if SECRET_PATH.exists():
        _restrict_secret_file(SECRET_PATH)
        lines = SECRET_PATH.read_text(encoding='utf-8').splitlines()
        pairs = [line.split('=', 1) for line in lines if '=' in line]
        values = dict(pairs)
        if (len(pairs) != 3 or len(values) != 3 or len(lines) != 3
                or set(values) != {STEM + suffix for suffix in ('URL', 'USER', 'PASSWORD')}
                or values[STEM + 'URL'] != URL or values[STEM + 'USER'] != PRINCIPAL
                or len(values[STEM + 'PASSWORD']) < 40):
            raise RuntimeError('Combined original-risk and entry-cost private credential has a different exact profile')
        return values[STEM + 'PASSWORD']
    if account_exists:
        raise RuntimeError('Existing original-risk principal lacks its private credential')
    # Restrict the empty exclusive file before writing any secret; an interrupted
    # empty file fails closed on retry and requires private operator recovery.
    with SECRET_PATH.open('x', encoding='utf-8'):
        pass
    _restrict_secret_file(SECRET_PATH)
    password = secrets.token_urlsafe(48)
    SECRET_PATH.write_text(f'{STEM}URL={URL}\n{STEM}USER={PRINCIPAL}\n'
                           f'{STEM}PASSWORD={password}\n', encoding='utf-8')
    _restrict_secret_file(SECRET_PATH)
    return password


def apply_with_clients(*, admin, credential, client_factory, policy, install_diagnostic=False):
    """Validate physical authority before credentials, account creation or grants."""
    policy = _policy(policy)
    if type(install_diagnostic) is not bool:
        raise ValueError('Diagnostic installation must be explicit')
    plan = desired_plan(policy)
    if admin.execute('SELECT currentUser()').strip() == PRINCIPAL:
        raise RuntimeError('Combined original-risk and entry-cost provisioning requires a distinct operator')
    present = admin.execute(f"SELECT count() FROM system.users WHERE name='{PRINCIPAL}' FORMAT TabSeparated").strip()
    if present not in {'0', '1'}:
        raise RuntimeError('Combined original-risk and entry-cost principal inventory is ambiguous')
    if install_diagnostic:
        # The exact workload policy must exist before CREATE can execute.
        rows = [json.loads(line) for line in admin.execute(
            "SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd' FORMAT JSONEachRow").splitlines()
                if line.strip()]
        if rows != [{'disks': ['live_market_ssd']}]:
            raise RuntimeError('Diagnostic installation requires SSD-only live_market_ssd')
        for ddl in diagnostic_install_plan(policy):
            admin.execute(ddl)
    storage_preflight(admin, tables=(*v4_storage_contracts(), ENTRY_SPREAD_RISK, DIAGNOSTIC,*selected_snapshot_contracts()))
    password = credential(account_exists=present == '1')
    if type(password) is not str or len(password) < 40:
        raise RuntimeError('Combined original-risk and entry-cost principal requires its private complete credential')
    if present == '0':
        digest = sha256(password.encode()).hexdigest()
        admin.execute(f"CREATE USER {PRINCIPAL} IDENTIFIED WITH sha256_hash BY '{digest}' "
                      "HOST IP '172.16.0.0/12', IP '127.0.0.1', IP '::1'")
    writer = client_factory(PRINCIPAL, password)
    try:
        if writer.execute('SELECT currentUser()').strip() != PRINCIPAL:
            raise RuntimeError('Combined original-risk and entry-cost credential authenticates as another principal')
        # Reject extra/inherited broad authority before adding any grants.
        have = _effective_grants(writer, plan)
        for privilege, database, table in sorted(_desired_grants(plan) - have):
            admin.execute(f'GRANT {privilege} ON {database}.{table} TO {PRINCIPAL}')
        writer.automatic_ladder_profile = False
        writer.entry_spread_risk_profile = True
        writer.confirmed_original_risk_policy = policy
        _v4_preflight(writer)
    finally:
        writer.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Apply only on the managed workstation')
    parser.add_argument('--confirm-policy', metavar='VERSION', help='Exact declared original-risk policy ID')
    parser.add_argument('--install-diagnostic', action='store_true', help='Operator creates declared diagnostic and snapshot tables if absent')
    parser.add_argument('--confirm-install-diagnostic', action='store_true', help='Explicitly approve that operator DDL')
    parser.add_argument('--strategy-number', required=True, type=int, help='Installed declaration selecting both journal capabilities')
    args = parser.parse_args(argv)
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    from src.trading_runtime.original_risk_diagnostic_profile import original_risk_runner_options
    initialize_numbered_fixed_strategies()
    try:
        contract = numbered_fixed_strategy(args.strategy_number)
        policy = contract.confirmed_original_risk_policy
        _policy(policy)
        if original_risk_runner_options(contract).get('entry_spread_risk') is not True:
            raise ValueError('Missing declared entry-cost capability')
    except ValueError:
        parser.error('--strategy-number must select the declared combined journal capability')
    plan = desired_plan(policy)
    if args.apply and args.confirm_policy != ENTRY_COST_CAPABILITY_RULE:
        parser.error('--apply requires --confirm-policy ' + ENTRY_COST_CAPABILITY_RULE)
    if args.install_diagnostic and (not args.apply or not args.confirm_install_diagnostic):
        parser.error('--install-diagnostic requires --apply and --confirm-install-diagnostic')
    if args.confirm_install_diagnostic and not args.install_diagnostic:
        parser.error('--confirm-install-diagnostic requires --install-diagnostic')
    print(f'Combined original-risk and entry-cost runner: {plan.principal}')
    print(f'Policy: {ENTRY_COST_CAPABILITY_RULE}; SELECT {len(plan.select_arte)}; INSERT {len(plan.insert_arte)}')
    if not args.apply:
        print('Plan only: no connection, credentials, grants or rows changed')
        print('Optional operator diagnostic-table DDL (runner receives no DDL):')
        for ddl in diagnostic_install_plan(policy):
            print(ddl)
        return 0
    admin = None
    try:
        if platform.node().upper() != 'DESKTOP-SAAI85T' or not SECRET_ROOT.is_dir():
            raise RuntimeError('Combined original-risk and entry-cost provisioning requires the managed workstation secret root')
        addresses = {row[4][0] for row in socket.getaddrinfo('desktop-saai85t', 18123,
            family=socket.AF_INET, type=socket.SOCK_STREAM)}
        if WORKSTATION_IPV4 not in addresses:
            raise RuntimeError('Pinned workstation transport is outside hostname resolution')
        from research.mlops.clickhouse import ClickHouseHttpClient
        transport = f'http://{WORKSTATION_IPV4}:18123'
        admin = _admin_client(transport)
        print('Active: verify SSD schema/parts, private account, exact grants and runner preflight')
        apply_with_clients(admin=admin, credential=credential, policy=policy,
            install_diagnostic=args.install_diagnostic,
            client_factory=lambda user, password: ClickHouseHttpClient(
                transport, user, password, timeout_seconds=60))
    except KeyboardInterrupt:
        print('Interrupted: state may be partial; rerun the same explicit plan after private review', file=sys.stderr)
        return 130
    except Exception as exc:
        # HTTP errors may contain authentication material/hash: no exception text.
        print(f'Blocked: {type(exc).__name__}; inspect privately, then rerun the same explicit plan', file=sys.stderr)
        return 1
    finally:
        if admin is not None:
            try:
                admin.close()
            except Exception as exc:
                print(f'Blocked closing operator: {type(exc).__name__}; inspect privately', file=sys.stderr)
                return 1
    print('Verified: exact combined grants and SSD schema/parts; 0 market or journal rows inserted')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
