"""Replay a frozen CUDA policy on held-out shards or configured diagnostic sessions."""
from __future__ import annotations

import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))

import argparse
import math
from time import perf_counter

import numpy as np
import torch
from rich.console import Console

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v1.data import SessionShard
from research.rl_trading.v1.features import FEATURE_NAMES, SECONDS
from research.rl_trading.v1.model import MarketPolicy
from research.rl_trading.v1.replay import replay_session
from src.market_engine.level_book_store import read, write
from src.runtime_paths import runtime_root

VERSION = 'rl-trading-closed-loop-replay-v2'


class ModelSelector:
    def __init__(self, model: MarketPolicy, data, shard: SessionShard, device):
        self.model = model
        self.data = data
        self.shard = shard
        self.device = device

    def __call__(self,state):
        plan = self.shard.plan
        slots = np.full(plan['top_n'],-1,dtype=np.int32)
        slots[:len(state['visible'])] = state['visible']
        override = torch.as_tensor(slots,device=self.device).unsqueeze(0)
        row = torch.tensor([state['index']],device=self.device)
        batch = self.data.batch(row,slot_override=override)
        batch['rank'][0,:len(state['rank'])] = torch.tensor(state['rank'],device=self.device)/max(1,len(plan['tickers']))
        batch['held'][0,:len(state['held'])] = torch.tensor(state['held'],device=self.device,dtype=torch.float32)
        account = (state['cash']/plan['initial_cash'],state['equity']/plan['initial_cash'],
                   state['second']/(SECONDS-1))
        batch['account'][0] = torch.tensor(account,device=self.device)
        batch['lots'].zero_()
        batch['lot_slots'].fill_(-1)
        for i,lot in enumerate(state['starting_lots']):
            batch['lot_slots'][0,i] = state['visible'].index(lot.ticker_index)
            batch['lots'][0,i] = torch.tensor((lot.quantity,lot.entry_price,
                max(0.,(state['time_us']-lot.entry_us)/1e6)/3600),device=self.device)
        with torch.inference_mode():
            encoded,context,held = self.model.encode(batch)
        previous = torch.zeros_like(context)

        def select(step,mask,previous_token):
            nonlocal previous
            with torch.inference_mode():
                if step:
                    token = torch.tensor([previous_token],device=self.device)
                    previous = previous+self.model.action_embedding(encoded,held,token)
                logits = self.model.action_logits(encoded,context,held,previous,step).float()
                allowed = torch.as_tensor(mask,device=self.device).unsqueeze(0)
                return int(logits.masked_fill(~allowed,-1e9).argmax(-1).item())
        return select


def run(args):
    if not torch.cuda.is_available():
        raise RuntimeError('Closed-loop replay requires CUDA')
    root = args.run.resolve()
    config = read(root/'config.json')
    compatible_diagnostic_changes = {'train.py','replay.py','evaluate_replay.py'}
    for name,expected in config['code_hashes'].items():
        if (file_hash(Path(__file__).with_name(name)) != expected and
                not (args.retrace_compatible and name in compatible_diagnostic_changes)):
            raise ValueError('Replay model code differs from checkpoint contract: '+name)
    checkpoint_name = (f'checkpoint_epoch_{args.checkpoint_epoch:012d}.pt'
        if args.checkpoint_epoch else {'best-val':'checkpoint_best_val.pt',
        'best-replay':'checkpoint_best_replay.pt','latest':'checkpoint_latest.pt'}[args.checkpoint])
    checkpoint = root/'checkpoints'/checkpoint_name
    saved = torch.load(checkpoint,map_location='cpu',weights_only=False)
    if saved['config_hash'] != config['config_hash']:
        raise ValueError('Replay checkpoint and run configuration differ')
    shards = [SessionShard(path) for path in args.test_shards]
    supplied_roots = {str(shard.root) for shard in shards}
    if not shards or len(supplied_roots) != len(shards):
        raise ValueError('Replay sessions must be distinct')
    if args.dataset == 'heldout':
        protected = {SessionShard(Path(path),verify=False).plan['date']
                     for path in config['train_shards']+config['val_shards']}
        test_days = {s.plan['date'] for s in shards}
        if protected & test_days or min(test_days) <= max(protected):
            raise ValueError('Held-out replay needs dates after all training and validation')
    else:
        split = 'train_shards' if args.dataset == 'train-diagnostic' else 'val_shards'
        if not supplied_roots <= {str(Path(path).resolve()) for path in config[split]}:
            raise ValueError('Diagnostic replay needs exact configured '+split)
    if not args.allow_segment and any(s.plan['segment'] for s in shards):
        raise ValueError('Segment replay requires explicit bounded-smoke permission')
    first = shards[0].plan
    contract = (first['top_n'],first['history_seconds'],first['max_lots'],
        first['max_orders'],first['feature_names'])
    if any((s.plan['top_n'],s.plan['history_seconds'],s.plan['max_lots'],
            s.plan['max_orders'],s.plan['feature_names']) != contract for s in shards):
        raise ValueError('Replay shard contracts differ')
    device = torch.device('cuda',args.device)
    model = MarketPolicy(features=len(FEATURE_NAMES),tickers=len(config['ticker_vocabulary']),
        top_n=first['top_n'],max_lots=first['max_lots'],max_orders=first['max_orders'],
        **config['model']).to(device)
    model.load_state_dict(saved['model'])
    model.eval()
    reports = []
    traces = []
    console = Console()
    for shard in shards:
        data = shard.to_gpu(device,config['ticker_vocabulary'])
        start = perf_counter()
        trace = {}
        with torch.inference_mode():
            result = replay_session(shard,ModelSelector(model,data,shard,device),
                max_seconds=args.max_seconds,trace=trace)
        torch.cuda.synchronize()
        wall_seconds = perf_counter()-start
        result.update(date=shard.plan['date'],shard=str(shard.root),
            shard_plan_hash=shard.plan['plan_hash'])
        result['teacher_optimality'] = shard.complete['teacher_optimality']
        result['teacher_profit'] = float(shard.complete['teacher_profit'])
        result['profit_minus_teacher'] = (result['profit']-result['teacher_profit']) if result['complete'] else None
        reports.append(result)
        traces.append((shard.plan['date'],shard.plan['plan_hash'],result,trace))
        console.print(f"{result['date']} | {result['seconds']:,} seconds | "
            f"equity ${result['terminal_equity']:,.2f} | "
            f"P&L ${result['profit']:,.2f} | drawdown {result['max_drawdown']:.2%} | "
            f'{wall_seconds:.1f}s')
        del data
    if args.dataset == 'val-diagnostic' and args.checkpoint_epoch:
        original = read(root/f'closed_loop_epoch_{args.checkpoint_epoch:03d}.json')
        by_day = {item['date']:item for item in original['validation']}
        for result in reports:
            prior = by_day[result['date']]
            for key in ('profit','fees_paid','max_drawdown','average_holding_seconds'):
                if not math.isclose(result[key],prior[key],rel_tol=1e-8,abs_tol=1e-5):
                    raise ValueError(f'Retrace differs from original {result["date"]} {key}')
            if result['buys'] != prior['buys'] or (result['sells']-
                    result['forced_liquidations'] != prior['voluntary_sells']):
                raise ValueError('Retrace trade counts differ from original validation replay')
    report = dict(version=VERSION,run=str(root),config_hash=config['config_hash'],
        checkpoint_hash=file_hash(checkpoint),checkpoint=str(checkpoint),
        dataset=args.dataset,retrace_compatible=args.retrace_compatible,sessions=reports,
        aggregate_profit=sum(item['profit'] for item in reports),
        complete=all(item['complete'] for item in reports))
    runtime = runtime_root().resolve()
    if not runtime.is_dir():
        raise ValueError('Required runtime root is unavailable')
    identity = digest({k:v for k,v in report.items() if k != 'sessions'} | dict(
        shard_hashes=[item['shard_plan_hash'] for item in reports],max_seconds=args.max_seconds))
    output = runtime/'rl-trading'/'v1'/'replay'/identity[:20]
    output.mkdir(parents=True,exist_ok=True)
    report['trace_files'] = []
    for day,plan_hash,summary,trace in traces:
        trace_path = output/f'positions_{day}.json'
        write(trace_path,dict(version='rl-trading-replay-trace-v1',date=day,
            config_hash=config['config_hash'],checkpoint_hash=report['checkpoint_hash'],
            shard_plan_hash=plan_hash,summary=summary,
            orders=trace['orders'],positions=trace['positions']))
        report['trace_files'].append(str(trace_path))
    write(output/'report.json',report)
    console.print('Replay report: '+str(output/'report.json'))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--test-shards',type=Path,nargs='+',required=True)
    parser.add_argument('--device',type=int,default=0)
    parser.add_argument('--checkpoint',choices=('best-val','best-replay','latest'),default='best-val')
    parser.add_argument('--checkpoint-epoch',type=int,default=0,
        help='Replay a saved archive epoch rather than a moving checkpoint alias')
    parser.add_argument('--dataset',choices=('heldout','train-diagnostic','val-diagnostic'),
        default='heldout',help='Diagnostics are restricted to configured train or validation shards')
    parser.add_argument('--retrace-compatible',action='store_true',
        help='Allow only replay/training logger code changes while preserving model and data hashes')
    parser.add_argument('--max-seconds',type=int,default=0,help='Bounded smoke only')
    parser.add_argument('--allow-segment',action='store_true')
    args = parser.parse_args(argv)
    if args.checkpoint_epoch < 0 or args.max_seconds < 0 or args.max_seconds and not args.allow_segment:
        parser.error('Bounded replay requires --allow-segment')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
