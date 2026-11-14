"""Read-only audit by default; explicit bounded profile or new phase-two training."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
if __package__ in (None, ''):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    __package__ = 'research.bar_gpt.phase2_v1'

import argparse
from dataclasses import replace
import json
from pathlib import Path
import random
import time
import numpy as np
import torch
from ..v5.shards.contract import digest, write_json, file_hash
from ..v5.reporting_v1 import Reporter
from .config import Config
from .data import load_units, checked_unit, plan_block
from .model import Forecaster, future_loss
from .tokenizer import load_teacher, SessionTokens
from .train import train, lease, autocast


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('audit', 'profile', 'train'), default='audit')
    p.add_argument('--execute', action='store_true', help='Required for optimizer training')
    p.add_argument('--dataset-root', type=Path, action='append', required=True,
                   help='Repeat for each explicitly selected completed immutable plan')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--teacher-checkpoint', type=Path)
    p.add_argument('--teacher-sha256')
    p.add_argument('--device', default='cpu', choices=('cpu', 'cuda'))
    p.add_argument('--precision', choices=('fp32', 'bf16'), default='fp32')
    p.add_argument('--batch-size', type=int, default=256)
    p.add_argument('--token-batch', type=int, default=256)
    p.add_argument('--cache-mib', type=int, default=512)
    p.add_argument('--threads', type=int, default=1)
    p.add_argument('--max-blocks', type=int, default=4, help='Audit/profile only; never limits training')
    p.add_argument('--profile-origins', type=int, default=16)
    p.add_argument('--profile-steps', type=int, default=3)
    p.add_argument('--epochs', type=int, default=10)
    p.add_argument('--learning-rate', type=float, default=3e-4)
    p.add_argument('--min-learning-rate', type=float, default=5e-5)
    p.add_argument('--warmup-updates', type=int, default=1000)
    p.add_argument('--patience', type=int, default=8)
    p.add_argument('--checkpoint-every', type=int, default=1000)
    p.add_argument('--log-every', type=int, default=10)
    p.add_argument('--seed', type=int, default=20261114)
    p.add_argument('--resume', type=Path)
    p.add_argument('--source-revision', default='', help='Required in native immutable snapshots without Git')
    p.add_argument('--wandb-mode', choices=('disabled', 'online', 'offline'), default='disabled')
    a = p.parse_args(argv)
    positive = ('batch_size', 'token_batch', 'cache_mib', 'threads', 'max_blocks',
                'profile_origins', 'profile_steps', 'epochs', 'patience', 'checkpoint_every', 'log_every')
    if any(getattr(a,k)<1 for k in positive) or not 0 < a.min_learning_rate <= a.learning_rate or a.warmup_updates < 0:
        p.error('Resource limits and schedule must be finite positive values')
    if not np.isfinite([a.learning_rate,a.min_learning_rate]).all():p.error('Nonfinite schedule')
    if a.mode == 'train' and not a.execute:p.error('Training requires --execute')
    if a.mode != 'audit' and (not a.teacher_checkpoint or not a.teacher_sha256):
        p.error('Profile/train requires an explicit V4 checkpoint and SHA256')
    if bool(a.teacher_checkpoint) != bool(a.teacher_sha256):p.error('Supply both teacher path and SHA256')
    if a.resume and a.mode != 'train':p.error('Resume is only meaningful for training')
    if a.mode != 'train' and a.wandb_mode != 'disabled':p.error('Audit/profile never creates a W&B run')
    a.output = a.output.resolve()
    if not a.output.is_relative_to(Path('D:/TradingML/runtimes').resolve()):
        p.error('Output must be beneath D:/TradingML/runtimes')
    return a


def bounded_check(units, teacher, proof, args):
    reports, measured, seen = [], [], 0
    model = Forecaster().to(args.device) if args.mode == 'profile' else None
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate) if model else None
    for unit in units:
        reader, session = checked_unit(unit), None
        for ordinal,s,liquid,selection in unit.blocks:
            plan = plan_block(reader, ordinal)
            report = dict(path=str(unit.path), split=s, liquid=liquid, selection=selection, **plan.report)
            if teacher is not None and len(plan.origins):
                if session is None:
                    session = SessionTokens(reader, teacher, proof['sha256'], device=args.device,
                                            precision=args.precision, memory_mib=args.cache_mib,
                                            token_batch=args.token_batch)
                n = min(args.profile_origins, len(plan.origins))
                small = replace(plan, origins=plan.origins[:n], endpoints=tuple(x[:n] for x in plan.endpoints),
                                targets=plan.targets[:n], origin_us=plan.origin_us[:n])
                began=time.perf_counter();session.prepare(small)
                if args.device == 'cuda':torch.cuda.synchronize()
                report['tokenization_seconds'] = time.perf_counter()-began
                tokens,metadata,target = session.batch(small,0,n)
                report.update(input_shape=list(tokens.shape), metadata_shape=list(metadata.shape),
                              target_shape=list(target.shape), unique_encoded_windows=session.encoded_windows)
                if model is not None:
                    began=time.perf_counter()
                    for _ in range(args.profile_steps):
                        optimizer.zero_grad(set_to_none=True)
                        with autocast(args):_, predicted=model(tokens,metadata);loss=future_loss(predicted,target)
                        loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
                        optimizer.step()
                    if args.device == 'cuda':torch.cuda.synchronize()
                    seconds=time.perf_counter()-began
                    measured.append(dict(origins=n*args.profile_steps, seconds=seconds,
                                         samples_per_second=n*args.profile_steps/seconds,
                                         loss=float(loss.detach()), device=args.device,
                                         note='bounded repeated-batch compute, not corpus throughput'))
            reports.append(report);seen+=1
            if seen>=args.max_blocks:break
        if seen>=args.max_blocks:break
    if not reports:raise ValueError('No selected blocks')
    if args.mode=='profile' and not measured:raise ValueError('No eligible profile origins')
    return dict(mode=args.mode, passed=True, blocks=reports, measured=measured,
                scope='bounded selected-block sample; not full-corpus eligibility certification')


def main(argv=None):
    a=arguments(argv)
    if (a.output/'STOP').exists():raise ValueError('Human STOP is present; it is never cleared by this launcher')
    if a.output.exists() and not a.resume:
        raise FileExistsError('Use a new output, or explicit --resume with exact contract')
    if a.resume and (not a.output.is_dir() or a.resume.resolve().parent!=a.output):
        raise ValueError('Resume requires a checkpoint in the same existing run output')
    torch.set_num_threads(a.threads)
    random.seed(a.seed);np.random.seed(a.seed);torch.manual_seed(a.seed)
    units,dataset=load_units(a.dataset_root)
    teacher=proof=None
    if a.teacher_checkpoint:teacher,proof=load_teacher(a.teacher_checkpoint,a.teacher_sha256,a.device)
    config=Config()
    sources=list(Path(__file__).parent.glob('*.py'))
    shared=Path(__file__).resolve().parents[1]/'v5'
    sources += [shared/name for name in ('encoder_v3.py','pretraining_v3.py','transforms_v1.py',
                                        'phase2_ar_shards/storage.py','phase2_ar_shards/contract.py')]
    implementation=digest({str(p.relative_to(Path(__file__).resolve().parents[1])):file_hash(p)
                           for p in sources})
    contract=dict(design=config.manifest(),dataset=dataset,teacher=proof,implementation=implementation,
                  tokenizer=dict(tile_batch=a.token_batch,precision=a.precision,
                                 algorithm='fixed-pool-endpoint-tiles-v1'),
                  numerics=dict(torch=str(torch.__version__),cuda=torch.version.cuda,
                                threads=a.threads,matmul=torch.get_float32_matmul_precision(),
                                tf32=bool(torch.backends.cuda.matmul.allow_tf32),
                                deterministic=torch.are_deterministic_algorithms_enabled(),
                                device_name=torch.cuda.get_device_name() if a.device=='cuda' else 'cpu'),
                  optimization=dict(batch=a.batch_size,precision=a.precision,device=a.device,
                      epochs=a.epochs,peak=a.learning_rate,minimum=a.min_learning_rate,
                      warmup=a.warmup_updates,weight_decay=.01,gradient_clip=1.,seed=a.seed,
                      patience=a.patience,order='root/session/block/eligible-origin chronological',
                      masking='.2 whole historical tokens; future target only'))
    # JSON-normalize tuples so checkpoint/reporting identities are platform stable.
    contract=json.loads(json.dumps(contract))
    a.output.mkdir(parents=True,exist_ok=bool(a.resume))
    with lease(a.output):
        if a.mode!='train':
            result=bounded_check(units,teacher,proof,a)
            result['contract']=contract;write_json(a.output/'result.json',result)
            print(json.dumps(result));return result
        manifest=dict(mode='train',model=contract,dataset=dataset['hash'],seed=a.seed,
                      precision=a.precision,batch_size=a.batch_size,epochs=a.epochs,
                      learning_rate=a.learning_rate,min_learning_rate=a.min_learning_rate,
                      warmup_updates=a.warmup_updates,resume=str(a.resume) if a.resume else None,
                      output=str(a.output),checkpoint=str(a.teacher_checkpoint),
                      source_revision=a.source_revision or source_revision())
        if not a.resume:write_json(a.output/'manifest.json',manifest)
        reporter=Reporter(a.output,manifest,mode=a.wandb_mode,project='bargpt-phase2-latent-v1')
        try:
            model=Forecaster(config).to(a.device)
            state=train(model,teacher,proof['sha256'],units,contract,a,reporter)
            reporter.finish(state)
            return dict(state=state)
        except BaseException as error:
            reporter.finish('failed',f'{type(error).__name__}: {error}');raise


def source_revision():
    import subprocess
    return subprocess.check_output(['git','rev-parse','HEAD'],text=True,
                                   cwd=Path(__file__).resolve().parents[3]).strip()


if __name__=='__main__':main()
