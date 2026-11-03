"""Frozen entry-score account replay on approved TRAINING dates only.

At the predictor's fixed five-minute sampling clocks, a flat account buys the
highest positive score among currently permitted entries. All other actions are
WAIT. Stops, targets, age exits, capacity, fees and the tail use Environment.step.
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
import torch

from research.market_shards.v1.contract import digest, sha, write_json
from research.rl_trading.v7.config import Config
from research.rl_trading.v7.data import TrainingDataset
from research.rl_trading.v7.experiments.cost_aware import CostEnvironment
from research.rl_trading.v7.experiments.predictive_data import sampled_seconds
from research.rl_trading.v7.stage_data import DEFAULT_DEST
from research.rl_trading.v7.train import source_identity


def replay_session(session, scorer, config=Config(), notify=None):
    env = CostEnvironment(session, config)
    schedule = set(sampled_seconds(session.day)[::scorer.row_step].tolist())
    fills, decisions = [], []
    last = time.monotonic()
    while not env.done:
        action = 0
        if not env.quantity and env.t in schedule:
            obs = env.observe()
            scores = scorer.score([obs])[0]
            allowed = obs['action_mask'][1:6]
            if scores.shape != (5,) or not np.isfinite(scores[allowed]).all():
                raise ValueError('Invalid eligible entry score')
            ranked = np.where(allowed, scores, -np.inf)
            slot = int(np.argmax(ranked))
            if ranked[slot] > 0:
                action = slot+1
            decisions.append(dict(second=env.t, action=action,
                scores=[float(v) if np.isfinite(v) else None for v in scores],
                allowed=allowed.tolist(), cash=env.cash))
        env.step(action)
        fills.extend(env.last_fills)
        if notify and time.monotonic()-last >= 20:
            notify(dict(second=env.t, entries=env.entries))
            last = time.monotonic()
    return dict(env.result(), fill_records=fills, decisions=decisions)


def main(argv=None, *, bundle_loader=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=DEFAULT_DEST)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--forecast', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dates', nargs='+')
    args = parser.parse_args(argv)
    data = TrainingDataset(args.source)
    dates = args.dates or data.days
    if len(set(dates)) != len(dates) or any(d not in data.days for d in dates):
        raise ValueError('Only unique approved TRAINING dates may be replayed')
    if bundle_loader is None:
        from research.rl_trading.v7.experiments.entry_score import load_bundle
        bundle_loader = load_bundle
    torch.set_num_threads(1)
    scorer = bundle_loader(args.bundle, data, args.forecast, device='cpu')
    root = args.output.resolve()
    runtime = Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):
        raise ValueError('Available runtime output required')
    contract = dict(dataset=data.binding, scorer=scorer.binding, source=sha(Path(__file__)),
        base_source=source_identity(), dates=dates, risk=Config().manifest(),
        decision_rule='highest currently allowed score strictly above zero; first slot breaks ties',
        schedule='sampled_seconds(day)[::row_step]', row_step=scorer.row_step,
        exit_rule='WAIT; original fixed stop/target/age/forced exit logic',
        evidence='frozen-policy independent account replay', validation_access=False)
    contract_hash = digest(contract)
    if (root/'manifest.json').exists():
        if json.loads((root/'manifest.json').read_text()) != dict(contract=contract, hash=contract_hash):
            raise ValueError('Frozen replay contract mismatch')
    else:
        root.mkdir(parents=True, exist_ok=False)
        write_json(root/'manifest.json', dict(contract=contract, hash=contract_hash))
    results = []
    started = time.monotonic()

    def status(phase, day=None, extra=None):
        write_json(root/'status.json', dict(status=phase, completed=len(results), active=day,
            queued=len(dates)-len(results)-(1 if day else 0),
            elapsed_seconds=time.monotonic()-started, **(extra or {})))

    try:
        for day in dates:
            path, seal = root/(day+'.json'), root/(day+'.complete.json')
            if seal.exists():
                if json.loads(seal.read_text()) != dict(sha256=sha(path), contract_hash=contract_hash):
                    raise ValueError('Replay day integrity mismatch')
                prior = json.loads(path.read_text())
                if not prior['valid_terminal']:
                    raise ValueError('Previously sealed unliquidated account remains failed')
                results.append(prior)
                continue
            if (root/'STOP').exists():
                status('stopped')
                return 2
            if path.exists():
                path.rename(root/(day+'.uncommitted-'+str(time.time_ns())+'.json'))
            status('running', day)
            session = data.load(day)
            outcome = replay_session(session, scorer, notify=lambda extra: status('running', day, extra))
            write_json(path, outcome)
            write_json(seal, dict(sha256=sha(path), contract_hash=contract_hash))
            results.append(outcome)
            write_json(root/'results.json', results)
            print(json.dumps({k:v for k,v in outcome.items() if k not in ('fill_records', 'decisions')}), flush=True)
            del session
            if not outcome['valid_terminal']:
                raise ValueError('Unliquidated terminal account; retained as failed qualification evidence')
        write_json(root/'results.json', results)
        status('complete')
    except BaseException as exc:
        status('failed', extra=dict(error=repr(exc)))
        raise
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
