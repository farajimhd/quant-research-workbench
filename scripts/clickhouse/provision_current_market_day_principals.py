"""Plan-only by default; provision two separately approved upstream roles.

Creates no tables or data and starts no producer, service or backtest. Uses
the shared exact-grant reconciler and private workstation credential writer.
"""
import os
import sys
from pathlib import Path

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import platform
import socket

from scripts.run_current_market_day import principal_plan, CurrentMarketDaySqlClient, MarketDayQueryLimits
from scripts.clickhouse.plan_program_app_principals import ProgramPrincipalPlan
from scripts.clickhouse.provision_program_app_principals import (
    approved_plan, apply_with_clients, credential, SECRET_ROOT, _admin_client, WORKSTATION_IPV4,
)


def desired_plans(years):
    return tuple(ProgramPrincipalPlan(principal, reads, writes)
        for principal, reads, writes in principal_plan(tuple(years)))


def storage_audit(admin):
    from scripts.build_market_day import storage_preflight
    from src.trading_runtime.arte_market_day_publisher import _placement
    # Shared metadata/storage audit only; this adapter never closes caller ownership.
    adapter = CurrentMarketDaySqlClient(admin, MarketDayQueryLimits(2, 256 * 1024**2, 60, 100000, 16000000))
    storage_preflight(adapter, 'arte', require_tables=True)
    _placement(admin)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-year', action='append', type=int, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--approved-plan')
    parser.add_argument('--confirm-current-market-day-principals', action='store_true')
    args = parser.parse_args(argv)
    plans = desired_plans(args.source_year)
    if not args.apply:
        if args.approved_plan or args.confirm_current_market_day_principals:
            parser.error('Approval arguments require --apply')
        print('PLAN ONLY | 2 upstream principals | No connections, accounts or data changes')
        for plan in plans:
            print(f'{plan.principal}: SELECT {len(plan.reads)} INSERT {len(plan.writes)}')
        return 0
    if not args.approved_plan or not args.confirm_current_market_day_principals:
        parser.error('--apply requires the exact approved plan and explicit confirmation')
    approved_plan(args.approved_plan, plans)
    if platform.node().upper() != 'DESKTOP-SAAI85T' or not SECRET_ROOT.is_dir():
        raise ValueError('Upstream provisioning requires the designated workstation secret root')
    addresses = {row[4][0] for row in socket.getaddrinfo('desktop-saai85t', 18123,
        family=socket.AF_INET, type=socket.SOCK_STREAM)}
    if WORKSTATION_IPV4 not in addresses:
        raise ValueError('Upstream transport differs from workstation hostname resolution')
    from research.mlops.clickhouse import ClickHouseHttpClient
    transport = f'http://{WORKSTATION_IPV4}:18123'
    def clients(user, password, *, readonly):
        return ClickHouseHttpClient(transport, user, password, timeout_seconds=60,
            default_query_params={'readonly': '1'} if readonly else None)
    admin = _admin_client(transport)
    try:
        apply_with_clients(admin=admin, plans=plans, credential_factory=credential,
            client_factory=clients, storage_audit=storage_audit,
            emit=lambda message: print(message, flush=True))
    finally:
        admin.close()
    print('COMPLETE | Exact grants verified | 0 rows inserted | No producers or runs started')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
