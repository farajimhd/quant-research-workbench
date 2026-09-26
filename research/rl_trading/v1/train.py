"""GPU-resident imitation baseline over immutable causal RL trading shards."""
from __future__ import annotations

import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))

import argparse
from contextlib import nullcontext
import math
import signal
from time import perf_counter

import torch
from rich.console import Console

from research.mlops.checkpoints import AsyncCheckpointManager, CheckpointPolicy
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.manifest import write_run_manifest
from research.mlops.metrics import AsyncJsonlMetricLogger
from research.mlops.paths import RunPaths
from research.mlops.schedulers import SampleWarmupCosineScheduler
from research.mlops.wandb_utils import init_wandb
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v1.data import SessionShard, ticker_vocabulary
from research.rl_trading.v1.features import FEATURE_NAMES
from research.rl_trading.v1.model import MarketPolicy
from research.rl_trading.v1.objectives import teacher_loss
from src.market_engine.level_book_store import read, write
from src.runtime_paths import runtime_root

VERSION = 'rl-trading-market-policy-bc-v1'
STOP = False


def _interrupt(*_):
    global STOP
    STOP = True


def _load_roots(paths, *, allow_segment):
    result = [SessionShard(Path(item)) for item in paths]
    if not result or len({item.plan['date'] for item in result}) != len(result):
        raise ValueError('Require one complete shard per distinct session date')
    if not allow_segment and any(item.plan['segment'] for item in result):
        raise ValueError('Segment shards are permitted only for a smoke test')
    contract = [(item.plan['top_n'],item.plan['history_seconds'],
                 item.plan['max_lots'],item.plan['max_orders'],item.plan['feature_names'])
                for item in result]
    if any(value != contract[0] for value in contract):
        raise ValueError('Training shard action or feature contracts disagree')
    return sorted(result,key=lambda item:item.plan['date'])


def _run_validation(model, sessions, device, batch_size, trade_weight, value_weight):
    model.eval()
    totals = dict(loss=0.,action_accuracy=0.,trade_recall=0.,samples=0)
    with torch.inference_mode():
        for data in sessions:
            for start in range(0,data.rows,batch_size):
                index = torch.arange(start,min(start+batch_size,data.rows),device=device)
                batch = data.batch(index)
                with torch.autocast('cuda',dtype=torch.bfloat16):
                    logits,value = model(batch,teacher_actions=batch['actions'])
                    loss,metrics = teacher_loss(logits,value,batch,
                        trade_weight=trade_weight,value_weight=value_weight)
                count = len(index)
                totals['loss'] += float(loss.detach())*count
                totals['action_accuracy'] += float(metrics['action_accuracy'])*count
                totals['trade_recall'] += float(metrics['trade_recall'])*count
                totals['samples'] += count
    model.train()
    return {key:value/max(1,totals['samples']) for key,value in totals.items() if key != 'samples'}


def run(args):
    global STOP
    STOP = False
    if not torch.cuda.is_available():
        raise RuntimeError('RL training requires CUDA; no CPU fallback is permitted')
    device = torch.device('cuda',args.device)
    torch.cuda.set_device(device)
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    train_shards = _load_roots(args.train_shards,allow_segment=args.allow_segment)
    val_shards = _load_roots(args.val_shards,allow_segment=args.allow_segment)
    train_days = {item.plan['date'] for item in train_shards}
    if train_days & {item.plan['date'] for item in val_shards}:
        raise ValueError('Training and validation sessions overlap')
    vocab = ticker_vocabulary(train_shards)
    contract = train_shards[0].plan
    if any((item.plan['top_n'],item.plan['history_seconds'],item.plan['max_lots'],
            item.plan['max_orders'],item.plan['feature_names']) !=
           (contract['top_n'],contract['history_seconds'],contract['max_lots'],
            contract['max_orders'],contract['feature_names']) for item in val_shards):
        raise ValueError('Validation feature or action contract differs')
    config = dict(version=VERSION,train_shards=[str(x.root) for x in train_shards],
        val_shards=[str(x.root) for x in val_shards],
        source_hashes={str(x.root):x.plan['plan_hash'] for x in train_shards+val_shards},
        feature_names=list(FEATURE_NAMES),ticker_vocabulary=vocab,
        model=dict(d_model=args.d_model,layers=args.layers,heads=args.heads),
        training=dict(epochs=args.epochs,batch_size=args.batch_size,learning_rate=args.learning_rate,
            weight_decay=args.weight_decay,grad_clip=args.grad_clip,
            trade_weight=args.trade_weight,value_weight=args.value_weight,
            seed=args.seed,allow_segment=args.allow_segment,archive_every=args.archive_every),
        code_hashes={name:file_hash(Path(__file__).with_name(name)) for name in
            ('train.py','data.py','model.py','objectives.py','features.py')})
    config['config_hash'] = digest(config)
    runtime = runtime_root().resolve()
    if not runtime.is_dir():
        raise ValueError('Required runtime root is unavailable')
    name = args.run_name or config['config_hash'][:16]
    paths = RunPaths.create(runtime/'rl-trading'/'v1'/'train'/name)
    config_path = paths.run_root/'config.json'
    if config_path.exists() and read(config_path) != config:
        raise ValueError('Existing run name belongs to a different training configuration')
    write(config_path,config)
    model = MarketPolicy(features=len(FEATURE_NAMES),tickers=len(vocab),
        top_n=contract['top_n'],max_lots=contract['max_lots'],
        max_orders=contract['max_orders'],d_model=args.d_model,
        layers=args.layers,heads=args.heads).to(device)
    optimizer = torch.optim.AdamW(model.parameters(),lr=args.learning_rate,
        weight_decay=args.weight_decay,fused=True)
    steps_per_epoch = sum(math.ceil(item.complete['rows']/args.batch_size) for item in train_shards)
    scheduler = SampleWarmupCosineScheduler(optimizer,
        warmup_samples=max(1,args.batch_size*min(100,steps_per_epoch//10)),
        total_samples=max(2,args.epochs*sum(item.complete['rows'] for item in train_shards)),
        minimum_lr=args.learning_rate*.05)
    start_epoch = 0
    global_step = 0
    latest = paths.checkpoints_dir/'checkpoint_latest.pt'
    if args.resume and latest.exists():
        saved = torch.load(latest,map_location=device,weights_only=False)
        if saved['config_hash'] != config['config_hash']:
            raise ValueError('Checkpoint belongs to a different run contract')
        model.load_state_dict(saved['model'])
        optimizer.load_state_dict(saved['optimizer'])
        scheduler.load_state_dict(saved['scheduler'])
        start_epoch = int(saved['epoch'])+1
        global_step = int(saved['global_step'])
    load_env_files(discover_env_files(REPO),verbose=False)
    wandb = init_wandb(entity=args.wandb_entity,project=args.wandb_project,
        run_name=name,config=config,run_dir=paths.wandb_dir,mode=args.wandb_mode,
        timeout_seconds=60) if args.wandb_mode != 'disabled' else None
    write_run_manifest(paths.manifest_path,repo_root=REPO,model_family='rl_trading',
        version=VERSION,job_type='train',run_name=name,args=vars(args),config=config,
        data_roots={str(i):str(shard.root) for i,shard in enumerate(train_shards+val_shards)},
        output_root=paths.run_root,source_checkpoint=latest if start_epoch else None,
        wandb_info={'project':args.wandb_project,'mode':args.wandb_mode})
    metrics = AsyncJsonlMetricLogger(paths.metrics_path,wandb)
    checkpoint = AsyncCheckpointManager(paths.checkpoints_dir,paths.checkpoint_manifest_path,
        CheckpointPolicy(latest_steps=1,archive_steps=max(1,args.archive_every),
            monitor_train_key='train/loss',monitor_val_key='val/loss',
            clock_name='epoch',archive_prefix='checkpoint_epoch'))
    console = Console()
    console.print(f'Training {name} | {len(train_shards)} train days | {len(val_shards)} validation days | '
        f'{len(vocab)} tickers | top {contract["top_n"]} | history {contract["history_seconds"]}s | '
        f'{sum(p.numel() for p in model.parameters()):,} parameters | {device}')
    previous = signal.signal(signal.SIGINT,_interrupt)
    try:
        preload_started = perf_counter()
        train_data = [shard.to_gpu(device,vocab,reserve_fraction=.25) for shard in train_shards]
        val_data = [shard.to_gpu(device,vocab,reserve_fraction=.25) for shard in val_shards]
        torch.cuda.synchronize()
        preload_seconds = perf_counter()-preload_started
        console.print(f'GPU-resident sessions ready | preload {preload_seconds:.1f}s | '
            f'allocated {torch.cuda.memory_allocated(device)/2**30:.1f} GiB')
        for epoch in range(start_epoch,args.epochs):
            if STOP or (paths.run_root/'STOP').exists():
                break
            model.train()
            sums = dict(loss=0.,action_accuracy=0.,trade_recall=0.,samples=0)
            wall_start = perf_counter()
            gpu_ms = 0.
            for shard,data in zip(train_shards,train_data):
                generator = torch.Generator(device=device).manual_seed(args.seed+epoch*1000003+
                    int(shard.plan['date'].replace('-','')))
                order = torch.randperm(data.rows,device=device,generator=generator)
                for start in range(0,data.rows,args.batch_size):
                    index = order[start:start+args.batch_size]
                    begin,end = torch.cuda.Event(enable_timing=True),torch.cuda.Event(enable_timing=True)
                    begin.record()
                    batch = data.batch(index)
                    optimizer.zero_grad(set_to_none=True)
                    with torch.autocast('cuda',dtype=torch.bfloat16):
                        logits,value = model(batch,teacher_actions=batch['actions'])
                        loss,measure = teacher_loss(logits,value,batch,
                            trade_weight=args.trade_weight,value_weight=args.value_weight)
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(),args.grad_clip)
                    optimizer.step()
                    end.record()
                    end.synchronize()
                    gpu_ms += begin.elapsed_time(end)
                    count = len(index)
                    global_step += 1
                    scheduler.step(global_step*args.batch_size)
                    sums['loss'] += float(loss.detach())*count
                    sums['action_accuracy'] += float(measure['action_accuracy'])*count
                    sums['trade_recall'] += float(measure['trade_recall'])*count
                    sums['samples'] += count
                    if args.max_steps and global_step >= args.max_steps:
                        break
                del batch,logits,value,loss,measure,index,order,generator
                if args.max_steps and global_step >= args.max_steps:
                    break
            torch.cuda.synchronize()
            elapsed = perf_counter()-wall_start
            train_result = {key:value/max(1,sums['samples']) for key,value in sums.items() if key != 'samples'}
            train_result.update(gpu_compute_fraction=min(1.,gpu_ms/1000/max(elapsed,1e-9)),
                preload_seconds=preload_seconds if epoch == start_epoch else 0.,
                samples_per_second=sums['samples']/max(elapsed,1e-9))
            if sums['samples'] == 0:
                raise ValueError('No training examples were processed')
            val = _run_validation(model,val_data,device,args.batch_size,
                args.trade_weight,args.value_weight)
            report = {**{'train/'+key:float(value) for key,value in train_result.items()},
                **{'val/'+key:float(value) for key,value in val.items()}}
            metrics.log(report,global_step)
            console.print(f'Epoch {epoch+1}/{args.epochs} | train {train_result["loss"]:.4f} | '
                f'val {val["loss"]:.4f} | GPU compute {train_result["gpu_compute_fraction"]:.1%} | '
                f'{train_result["samples_per_second"]:,.0f} samples/s | {elapsed:.1f}s')
            checkpoint.maybe_save(step=epoch+1,force=True,train_metrics={'train/loss':train_result['loss']},
                val_metrics={'val/loss':val['loss']},payload_factory=lambda:dict(
                    config_hash=config['config_hash'],epoch=epoch,global_step=global_step,
                    model=model.state_dict(),optimizer=optimizer.state_dict(),
                    scheduler=scheduler.state_dict(),ticker_vocabulary=vocab))
            if args.require_gpu_bound and train_result['gpu_compute_fraction'] < args.min_gpu_fraction:
                raise RuntimeError(f'Training is not GPU-bound: measured compute fraction '
                    f'{train_result["gpu_compute_fraction"]:.1%} < {args.min_gpu_fraction:.1%}')
            if args.max_steps and global_step >= args.max_steps:
                break
        return 0
    finally:
        signal.signal(signal.SIGINT,previous)
        checkpoint.close(wait=True,timeout=180)
        metrics.close()
        if wandb is not None:
            wandb.finish()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--train-shards',type=Path,nargs='+',required=True)
    parser.add_argument('--val-shards',type=Path,nargs='+',required=True)
    parser.add_argument('--run-name',default='')
    parser.add_argument('--epochs',type=int,default=10)
    parser.add_argument('--batch-size',type=int,default=256)
    parser.add_argument('--device',type=int,default=0)
    parser.add_argument('--d-model',type=int,default=256)
    parser.add_argument('--layers',type=int,default=4)
    parser.add_argument('--heads',type=int,default=8)
    parser.add_argument('--learning-rate',type=float,default=3e-4)
    parser.add_argument('--weight-decay',type=float,default=.01)
    parser.add_argument('--grad-clip',type=float,default=1.)
    parser.add_argument('--trade-weight',type=float,default=4.)
    parser.add_argument('--value-weight',type=float,default=.1)
    parser.add_argument('--seed',type=int,default=17)
    parser.add_argument('--archive-every',type=int,default=5)
    parser.add_argument('--max-steps',type=int,default=0,help='Bounded smoke validation only')
    parser.add_argument('--allow-segment',action='store_true',help='Allow bounded segment shards for smoke validation')
    parser.add_argument('--require-gpu-bound',action=argparse.BooleanOptionalAction,default=True)
    parser.add_argument('--min-gpu-fraction',type=float,default=.7)
    parser.add_argument('--wandb-mode',choices=('disabled','auto','offline','online'),default='disabled')
    parser.add_argument('--wandb-project',default='rl-trading-v1')
    parser.add_argument('--wandb-entity',default='')
    parser.add_argument('--resume',action='store_true')
    args = parser.parse_args(argv)
    if (args.epochs < 1 or args.batch_size < 1 or args.max_steps < 0 or args.archive_every < 1
            or not 0 < args.min_gpu_fraction <= 1):
        parser.error('Training budgets must be positive')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
