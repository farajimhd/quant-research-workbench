"""Compare certified dynamic teacher sessions with their prior fixed-lot teachers.

The sealed test session is deliberately excluded. Reports are diagnostic: the
teachers use different price, sizing, and fee contracts, so profit is not a
like-for-like objective comparison.
"""
from __future__ import annotations

import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import argparse
import json
from collections import Counter

import polars as pl

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v1.phase3_dynamic_teacher import VERSION as DYNAMIC_VERSION
from research.rl_trading.v1.phase3_search import VERSION as PRIOR_VERSION
from src.market_engine.level_book_store import read, write
from src.runtime_paths import runtime_root

VERSION = 'rl-dynamic-teacher-comparison-v1'


def _certified(root: Path, expected_version: str) -> tuple[dict, dict]:
    plan, complete = read(root/'plan.json'), read(root/'complete.json')
    if (plan.get('version') != expected_version or
            plan.get('plan_hash') != digest({k:v for k,v in plan.items() if k!='plan_hash'}) or
            complete.get('plan_hash') != plan['plan_hash']):
        raise ValueError(f'Invalid teacher certificate: {root}')
    return plan, complete


def _prior_orders(path: Path) -> dict:
    counts = Counter()
    fees = 0.0
    gross = 0.0
    ticker_entries = set()
    for row in pl.scan_parquet(path).filter(pl.col('action') == 'trade').select(
            'time_us', 'action_legs_json').collect().iter_rows(named=True):
        for leg in json.loads(row['action_legs_json']):
            action = leg['action']
            if action not in ('buy', 'sell'):
                raise ValueError(f'Unknown prior action {action}')
            counts[action] += 1
            fees += float(leg['fee'])
            if action == 'buy':
                ticker_entries.add((int(row['time_us']), leg['ticker']))
            else:
                gross += float(leg['realized_pnl'])
    return dict(buys=counts['buy'], sells=counts['sell'], fees=fees,
                realized_net_from_legs=gross, unique_ticker_entry_seconds=len(ticker_entries))


def audit_day(supervision_root: Path, runtime: Path) -> dict:
    supervision, supervision_done = read(supervision_root/'plan.json'), read(supervision_root/'complete.json')
    if (supervision.get('scope') != 'full_session' or
            supervision.get('split') not in ('train', 'development') or
            supervision_done.get('plan_hash') != supervision.get('plan_hash') or
            file_hash(supervision_root/'orders.parquet') != supervision_done.get('orders_hash')):
        raise ValueError(f'Uncertified or sealed supervision: {supervision_root}')
    dynamic_root = Path(supervision['phase3_root']).resolve()
    if not dynamic_root.is_relative_to(runtime):
        raise ValueError('Dynamic teacher root outside runtime')
    dynamic, dynamic_done = _certified(dynamic_root, DYNAMIC_VERSION)
    prior_root = Path(dynamic['v7_population_source']).resolve()
    if not prior_root.is_relative_to(runtime):
        raise ValueError('Prior teacher root outside runtime')
    prior, prior_done = _certified(prior_root, PRIOR_VERSION)
    if (prior['date'] != dynamic['date'] or dynamic['date'] != supervision['date'] or
            dynamic['scope'] != 'full_session' or
            dynamic_done['trajectory_rows'] != 57_481 or
            prior_done['seconds'] != 57_481 or
            file_hash(prior_root/'complete.json') != dynamic['v7_population_certificate_hash'] or
            file_hash(dynamic_root/'complete.json') != supervision['phase3_complete_hash']):
        raise ValueError('Teacher dates, completeness, or population proof differ')
    for name, expected in dynamic_done['files'].items():
        if file_hash(dynamic_root/name) != expected:
            raise ValueError(f'Dynamic teacher file changed: {name}')
    for item in prior_done['files'].values():
        if file_hash(prior_root/item['file']) != item['file_hash']:
            raise ValueError(f'Prior teacher file changed: {item["file"]}')
    orders = pl.read_parquet(supervision_root/'orders.parquet')
    positions = pl.read_parquet(dynamic_root/'positions.parquet')
    trajectory = pl.read_parquet(dynamic_root/'trajectory.parquet')
    prior_orders = _prior_orders(prior_root/'trajectory.parquet')
    report = dynamic_done['report']
    dynamic_buys = orders.filter(pl.col('action') == 'buy')
    dynamic_sells = orders.filter(pl.col('action') == 'sell')
    new_fees = float(positions['entry_fee'].sum()+positions['exit_fee'].sum()) if positions.height else 0.0
    new_net = float(report['net_profit'])
    old_net = float(prior_done['terminal_profit'])
    if (dynamic_buys.height != positions.height or dynamic_sells.height != positions.height or
            report['buys'] != positions.height or report['sells'] != positions.height or
            abs(float(positions['net_pnl'].sum())-new_net) > 1e-5 or
            abs(float(trajectory['equity'][-1])-float(dynamic['initial_cash'])-new_net) > 1e-5 or
            prior_orders['buys'] != prior_orders['sells'] or
            abs(prior_orders['realized_net_from_legs']-old_net) > 1e-4):
        raise ValueError('Teacher orders, position counts, and P&L do not reconcile')
    return dict(date=dynamic['date'], split=supervision['split'],
        dynamic=dict(net_profit=new_net, gross_before_fees=new_net+new_fees,
            fees=new_fees, completed_positions=positions.height,
            buys=dynamic_buys.height, sells=dynamic_sells.height,
            max_open_positions=int(report['max_open_lots']),
            max_drawdown=float(report['max_drawdown']),
            session_periods=report['session_periods']),
        prior=dict(net_profit=old_net,
            gross_before_fees=old_net+prior_orders['fees'],
            fees=prior_orders['fees'], completed_positions=prior_orders['sells'],
            buys=prior_orders['buys'], sells=prior_orders['sells'],
            unique_ticker_entry_seconds=prior_orders['unique_ticker_entry_seconds'],
            max_open_positions=int(prior['config']['max_lots'])),
        comparable_contract=False,
        contract_differences=['close-price episode V5 versus prior price-action labels',
            'dynamic fractional uncapped sizing versus fixed $2500 lots, four open lots',
            'IBKR fixed commission versus IBKR Pro Tiered proxy',
            'different entry and target timing'],
        supervision_root=str(supervision_root),dynamic_root=str(dynamic_root),prior_root=str(prior_root))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split-manifest', required=True, type=Path)
    args = parser.parse_args(argv)
    runtime = runtime_root().resolve()
    manifest_path = args.split_manifest.resolve()
    if not runtime.is_dir() or not manifest_path.is_relative_to(runtime):
        raise ValueError('Certified split manifest must reside under runtime root')
    manifest = read(manifest_path)
    if manifest.get('version') != 'rl-dynamic-forward-split-v1' or len(manifest['train']) != 17 or len(manifest['development']) != 2 or len(manifest['test_sealed']) != 1:
        raise ValueError('Unexpected forward split inventory')
    rows = []
    for split in ('train', 'development'):
        for item in manifest[split]:
            root = Path(item['root']).resolve()
            if not root.is_relative_to(runtime) or file_hash(root/'complete.json') != item['complete_hash']:
                raise ValueError('Supervision split certificate changed')
            row = audit_day(root, runtime)
            if row['split'] != split or row['date'] != item['date']:
                raise ValueError('Split identity mismatch')
            rows.append(row)
    def total(side, field):
        return sum(row[side][field] for row in rows)
    totals = {side:{key:total(side,key) for key in ('net_profit','gross_before_fees','fees',
        'completed_positions','buys','sells')} for side in ('dynamic','prior')}
    comparison = dict(version=VERSION, split_manifest=str(manifest_path),
        split_manifest_hash=file_hash(manifest_path), days=rows, totals=totals,
        expectation=dict(fewer_completed_positions=totals['dynamic']['completed_positions'] < totals['prior']['completed_positions'],
            higher_modeled_net_profit=totals['dynamic']['net_profit'] > totals['prior']['net_profit']),
        interpretation='Diagnostic only: price labels, order sizing, and fee contracts differ. The sealed test is excluded.')
    comparison['report_hash'] = digest(comparison)
    output = runtime/'rl-trading-dynamic-supervision'/'audits'/comparison['report_hash'][:20]
    output.mkdir(parents=True, exist_ok=True)
    write(output/'comparison.json', comparison)
    print(output)
    print(json.dumps(dict(totals=totals,expectation=comparison['expectation']),indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
