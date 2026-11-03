"""Training-only single-entry feasibility audit using the unchanged simulator.

These hindsight outcomes are diagnostic targets, never causal observations.
The accelerated path skips only holding clocks before the first fixed exit
trigger; every fill and fee still runs through Environment.step/_fill.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
import argparse
import json
import time
import numpy as np

from research.market_shards.v1.contract import digest, sha, write_json
from research.rl_trading.v7.config import Config
from research.rl_trading.v7.environment import Environment
from research.rl_trading.v7.experiments.predictive_data import PredictiveDataset
from research.rl_trading.v7.stage_data import DEFAULT_DEST
from research.rl_trading.v7.train import source_identity


def single_entry(session, second, slot, config=Config(), *, accelerated=True):
    env = Environment(session, config)
    if not 0 <= second < len(session.selection) or not 0 <= slot < 5:
        raise ValueError('Invalid decision coordinates')
    env.t = int(second)
    action = int(slot)+1
    if not env.observe()['action_mask'][action]:
        return dict(status='ineligible_now', net_dollars=None, fills=[])
    env.step(action)
    fills = list(env.last_fills)
    if not env.entries:
        return dict(status='unfilled', net_dollars=0., fills=fills)
    if accelerated and env.quantity and not env.done:
        # At each source clock the reference checks thresholds, then executes
        # at the NEXT second. The forced-exit threshold is identical to step().
        end = min(env.entry+config.max_hold_seconds, len(session.clocks)-901,
                  len(session.clocks)-2)
        end = max(env.t, end)
        marks = env.e['mark'][env.t:end+1, env.ticker]
        hits = np.flatnonzero((marks <= env.basis*(1-config.stop_ratio)) |
                              (marks >= env.basis*(1+config.target_ratio)))
        jump = int(hits[0]) if len(hits) else len(marks)-1
        if not np.isfinite(marks[:jump+1]).all() or np.any(marks[:jump+1] <= 0):
            raise ValueError('Held position lacks causal positive mark')
        env.t += jump
    while env.quantity and not env.done:
        env.step(0)
        fills.extend(env.last_fills)
    closed = env.quantity == 0
    return dict(status='closed' if closed else 'unliquidated',
                net_dollars=env.cash-config.initial_cash if closed else None,
                fees=env.fees, quantity=fills[0]['quantity'],
                exit_second=env.t, terminal_quantity=env.quantity, fills=fills)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=DEFAULT_DEST)
    parser.add_argument('--snapshots', default='D:/TradingML/runtimes/rl-trading/v7/predictive-data-v1')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--dates', nargs='+')
    parser.add_argument('--row-step', type=int, default=10)
    parser.add_argument('--parity-count', type=int, default=4)
    args = parser.parse_args(argv)
    data = PredictiveDataset(args.source, args.snapshots)
    args.dates = args.dates or data.days
    if (len(set(args.dates)) != len(args.dates) or any(d not in data.days for d in args.dates)
            or args.row_step < 1 or args.parity_count < 1):
        raise ValueError('Unique TRAINING dates and positive audit sampling required')
    root = args.output.resolve()
    runtime = Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):
        raise ValueError('Available runtime output required')
    contract = dict(source=data.binding, code=sha(Path(__file__)), base_source=source_identity(),
        config=Config().manifest(), dates=args.dates, row_step=args.row_step,
        parity_count=args.parity_count, target='single entry then WAIT with fixed exits; flat initial cash',
        evidence='hindsight opportunity diagnostic; not policy performance or PPO trajectories',
        excluded_metrics='skipped per-second rewards, peak equity and drawdown', validation_access=False)
    contract['hash'] = digest(contract)
    if (root/'manifest.json').exists():
        if json.loads((root/'manifest.json').read_text()) != contract:
            raise ValueError('Audit contract mismatch')
    else:
        root.mkdir(parents=True, exist_ok=False)
        write_json(root/'manifest.json', contract)
    completed = []
    for day in args.dates:
        if (root/'STOP').exists():
            write_json(root/'status.json', dict(status='stopped', completed=completed))
            return 2
        target = root/(day+'.json')
        seal = root/(day+'.complete.json')
        if seal.exists():
            if json.loads(seal.read_text()) != dict(sha256=sha(target), contract_hash=contract['hash']):
                raise ValueError('Day audit integrity mismatch')
            existing = json.loads(target.read_text())
            if existing['contract_hash'] != contract['hash']:
                raise ValueError('Day audit contract mismatch')
            completed.append(day)
            continue
        if target.exists():
            target.rename(root/(day+'.uncommitted-'+str(time.time_ns())+'.json'))
        write_json(root/'status.json', dict(status='running', active=day, completed=completed,
                                           queued=len(args.dates)-len(completed)-1))
        started = last_progress = time.monotonic()
        snapshots = data.load(day)
        session = data.source.load(day)
        records, parity = [], 0
        for row in range(0, len(snapshots['seconds']), args.row_step):
            second = int(snapshots['seconds'][row])
            for slot in range(5):
                outcome = single_entry(session, second, slot)
                if outcome['status'] == 'closed' and parity < args.parity_count:
                    reference = single_entry(session, second, slot, accelerated=False)
                    if outcome != reference:
                        raise ValueError('Reference simulator parity mismatch')
                    parity += 1
                records.append(dict(row=row, second=second, slot=slot, **outcome))
            if time.monotonic()-last_progress >= 20:
                write_json(root/'status.json', dict(status='running', active=day, completed=completed,
                    sampled_rows=row//args.row_step+1, records=len(records),
                    queued=len(args.dates)-len(completed)-1))
                last_progress = time.monotonic()
        counts = {name: sum(r['status']==name for r in records)
                  for name in ('ineligible_now', 'unfilled', 'closed', 'unliquidated')}
        net = np.array([r['net_dollars'] for r in records if r['status']=='closed'])
        report = dict(day=day, contract_hash=contract['hash'], counts=counts,
            positive=int((net > 0).sum()), closed_mean_net_dollars=float(net.mean()) if len(net) else None,
            max_net_dollars=float(net.max()) if len(net) else None,
            parity_checked=parity, elapsed_seconds=time.monotonic()-started, records=records)
        write_json(target, report)
        write_json(seal, dict(sha256=sha(target), contract_hash=contract['hash']))
        print(json.dumps({k:v for k,v in report.items() if k!='records'}), flush=True)
        completed.append(day)
        del session
    write_json(root/'status.json', dict(status='complete', completed=completed, validation_access=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        if '--output' in sys.argv:
            root = Path(sys.argv[sys.argv.index('--output')+1]).resolve()
            status = root/'status.json'
            if status.exists() and root.is_relative_to(Path('D:/TradingML/runtimes').resolve()):
                previous = json.loads(status.read_text())
                write_json(status, dict(previous, status='failed', error=repr(exc)))
        raise
