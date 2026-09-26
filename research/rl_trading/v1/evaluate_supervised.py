"""Evaluate a frozen policy on disjoint held-out session shards.

This reports teacher agreement and return prediction; it is not a portfolio
replay or a profitability estimate.
"""
from __future__ import annotations

import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))

import argparse
import json

import torch
from rich.console import Console

from research.rl_trading.v1.common import file_hash
from research.rl_trading.v1.data import SessionShard
from research.rl_trading.v1.features import FEATURE_NAMES
from research.rl_trading.v1.model import MarketPolicy
from research.rl_trading.v1.objectives import teacher_loss
from src.market_engine.level_book_store import read


def run(args):
    if not torch.cuda.is_available():
        raise RuntimeError('Held-out policy evaluation requires CUDA')
    root = args.run.resolve()
    config = read(root/'config.json')
    for name,expected in config['code_hashes'].items():
        if file_hash(Path(__file__).with_name(name)) != expected:
            raise ValueError('Evaluation code differs from the checkpoint contract: '+name)
    checkpoint_name = {'best-val':'checkpoint_best_val.pt',
        'best-replay':'checkpoint_best_replay.pt','latest':'checkpoint_latest.pt'}[args.checkpoint]
    checkpoint = root/'checkpoints'/checkpoint_name
    saved = torch.load(checkpoint,map_location='cpu',weights_only=False)
    if saved['config_hash'] != config['config_hash']:
        raise ValueError('Checkpoint and run configuration differ')
    shards = [SessionShard(path) for path in args.test_shards]
    if not args.allow_segment and any(s.plan['segment'] for s in shards):
        raise ValueError('Segment test shards require --allow-segment for a bounded smoke test')
    train_val = {SessionShard(Path(path),verify=False).plan['date']
                 for path in config['train_shards']+config['val_shards']}
    if not shards or len({s.plan['date'] for s in shards}) != len(shards):
        raise ValueError('Test shards must cover distinct sessions')
    if train_val & {s.plan['date'] for s in shards}:
        raise ValueError('Held-out test session overlaps training or validation')
    first = shards[0].plan
    if any((s.plan['top_n'],s.plan['history_seconds'],s.plan['max_lots'],
            s.plan['max_orders'],s.plan['feature_names']) !=
           (first['top_n'],first['history_seconds'],first['max_lots'],
            first['max_orders'],first['feature_names']) for s in shards):
        raise ValueError('Held-out shard contracts differ')
    if tuple(first['feature_names']) != FEATURE_NAMES:
        raise ValueError('Held-out feature version differs')
    vocab = config['ticker_vocabulary']
    device = torch.device('cuda',args.device)
    model = MarketPolicy(features=len(FEATURE_NAMES),tickers=len(vocab),
        top_n=first['top_n'],max_lots=first['max_lots'],max_orders=first['max_orders'],
        **config['model']).to(device)
    model.load_state_dict(saved['model'])
    model.eval()
    totals = dict(seconds=0,orders=0,teacher_actions=0,correct_orders=0,
                  correct_trades=0,teacher_buys=0,correct_buys=0,
                  teacher_sells=0,correct_sells=0,predicted_buys=0,
                  feasible_first_buys=0,first_order_buys=0,
                  action_loss=0.,value_loss=0.)
    with torch.inference_mode():
        for shard in shards:
            data = shard.to_gpu(device,vocab)
            for offset in range(0,data.rows,args.batch_size):
                index = torch.arange(offset,min(offset+args.batch_size,data.rows),device=device)
                batch = data.batch(index)
                logits,value = model(batch,teacher_actions=batch['actions'])
                _,metrics = teacher_loss(logits,value,batch,
                    trade_weight=config['training']['trade_weight'],
                    value_weight=config['training']['value_weight'])
                prediction = logits.float().masked_fill(~batch['action_mask'],-1e9).argmax(-1)
                teacher = batch['actions']
                correct = prediction == teacher
                totals['seconds'] += len(index)
                totals['orders'] += teacher.numel()
                totals['teacher_actions'] += int((teacher != 0).sum())
                totals['correct_orders'] += int(correct.sum())
                totals['correct_trades'] += int((correct & (teacher != 0)).sum())
                buys = (teacher > 0) & (teacher <= first['top_n'])
                sells = teacher > first['top_n']
                totals['teacher_buys'] += int(buys.sum())
                totals['correct_buys'] += int((correct & buys).sum())
                totals['teacher_sells'] += int(sells.sum())
                totals['correct_sells'] += int((correct & sells).sum())
                totals['predicted_buys'] += int(((prediction > 0) &
                    (prediction <= first['top_n'])).sum())
                totals['feasible_first_buys'] += int(batch['action_mask'][:,0,
                    1:1+first['top_n']].any(dim=-1).sum())
                totals['first_order_buys'] += int(((prediction[:,0] > 0) &
                    (prediction[:,0] <= first['top_n'])).sum())
                totals['action_loss'] += float(metrics['action_loss'])*len(index)
                totals['value_loss'] += float(metrics['value_loss'])*len(index)
            del data
    report = dict(evaluation='teacher_forced_supervised',run=str(root),
        checkpoint_hash=file_hash(checkpoint),test_sessions=[s.plan['date'] for s in shards],
        test_shard_hashes={str(s.root):s.plan['plan_hash'] for s in shards},
        seconds=totals['seconds'],teacher_trade_actions=totals['teacher_actions'],
        order_accuracy=totals['correct_orders']/max(1,totals['orders']),
        teacher_trade_recall=totals['correct_trades']/max(1,totals['teacher_actions']),
        teacher_buys=totals['teacher_buys'],teacher_sells=totals['teacher_sells'],
        buy_recall=totals['correct_buys']/max(1,totals['teacher_buys']),
        sell_recall=totals['correct_sells']/max(1,totals['teacher_sells']),
        predicted_buys=totals['predicted_buys'],
        feasible_first_buys=totals['feasible_first_buys'],
        first_order_buys=totals['first_order_buys'],
        action_loss=totals['action_loss']/max(1,totals['seconds']),
        value_loss=totals['value_loss']/max(1,totals['seconds']))
    Console().print(json.dumps(report,indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--test-shards',type=Path,nargs='+',required=True)
    parser.add_argument('--batch-size',type=int,default=256)
    parser.add_argument('--device',type=int,default=0)
    parser.add_argument('--checkpoint',choices=('best-val','best-replay','latest'),default='best-val')
    parser.add_argument('--allow-segment',action='store_true')
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error('batch-size must be positive')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
