"""Read-only audit by default; explicit bounded profile or new phase-two training."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
if __package__ in (None, ''):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    __package__ = 'research.bar_gpt.phase2_v2'

import argparse
from dataclasses import replace
import json
from pathlib import Path
import random
import time
import numpy as np
import torch
from ..v5.shards.contract import digest, write_json, file_hash
from research.mlops.env import discover_env_files, load_env_files
from .config import Config
from .data import load_units, checked_unit, plan_block
from .model import Forecaster, future_loss
from .tokenizer import load_teacher, SessionTokens
from .train import train, lease, autocast, optimizer_for
from .metrics import CONTRACT as RECONSTRUCTION_CONTRACT, DecodedMetrics
from .teacher_selection import select_training_teacher
from .reporting import PROJECT, online_reporter, require_online_credentials
from .schedule import schedule_manifest


def arguments(argv=None, *, expected_feature_drop=320/512):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('audit', 'profile', 'train'), default='audit')
    p.add_argument('--execute', action='store_true', help='Required for optimizer training')
    data=p.add_mutually_exclusive_group(required=True)
    data.add_argument('--dataset-root', type=Path, action='append',
                   help='Repeat for each explicitly selected completed immutable plan')
    data.add_argument('--dataset-release',type=Path,help='Certified all-published-block certificate.json')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--teacher-checkpoint', type=Path)
    p.add_argument('--teacher-sha256')
    p.add_argument('--device', default='cpu', choices=('cpu', 'cuda'))
    p.add_argument('--precision', choices=('fp32', 'bf16'), default='fp32')
    p.add_argument('--batch-size', type=int, default=256)
    p.add_argument('--feature-drop-rate',type=float,default=expected_feature_drop,
                   help='V2 fixed320/512 coordinate-token masking')
    p.add_argument('--token-batch', type=int, default=256)
    p.add_argument('--cache-mib', type=int, default=512)
    p.add_argument('--threads', type=int, default=1)
    p.add_argument('--io-workers',type=int,default=4)
    p.add_argument('--prefetch-mib',type=int,default=2048)
    p.add_argument('--gpu-prefetch',action='store_true',help='Overlap frozen preparation on one bounded CUDA stream')
    p.add_argument('--prepare-workers',type=int,default=0,help='Ordered publication preparation pool,0=serial,maximum8')
    p.add_argument('--cuda-step-replay',action='store_true',help='Capture BF16 forward/backward/clip; optimizer/RNG remain live')
    p.add_argument('--student-attention-backend',choices=('default','cudnn'),default='default',
                   help='CUDA replay backend; exact approved architecture/precision unchanged')
    p.add_argument('--optimizer-backend',choices=('auto','fused'),default='auto')
    p.add_argument('--attention-layout',choices=('torch','batch-first-sdpa'),default='batch-first-sdpa')
    p.add_argument('--no-pack-batches',dest='pack_batches',action='store_false',
                   help='Profile baseline only: end every minibatch at a block boundary')
    p.add_argument('--max-blocks', type=int, default=4, help='Audit/profile only; never limits training')
    p.add_argument('--profile-origins', type=int, default=16)
    p.add_argument('--profile-steps', type=int, default=3)
    p.add_argument('--epochs', type=int, default=10,help='Number of complete training-dataset passes')
    p.add_argument('--learning-rate', type=float, default=3e-4)
    p.add_argument('--min-learning-rate', type=float, default=5e-5)
    p.add_argument('--warmup-updates', type=int, default=2000)
    p.add_argument('--lr-restart-decay',type=float,default=.9,
                   help='Multiply each new FULL DATASET PASS peak/floor by this factor')
    p.add_argument('--patience', type=int, default=8)
    p.add_argument('--checkpoint-every', type=int, default=1000)
    p.add_argument('--validation-every', type=int, default=150000,
                   help='Full validation and LR-update frequency in optimizer updates; never the epoch length;must be positive')
    p.add_argument('--log-every', type=int, default=10)
    p.add_argument('--seed', type=int, default=20261114)
    p.add_argument('--resume', type=Path)
    p.add_argument('--source-revision', default='', help='Required in native immutable snapshots without Git')
    p.add_argument('--wandb-mode', choices=('disabled', 'online', 'offline'), default=None,
                   help='Training requires online; audit/profile use disabled')
    a = p.parse_args(argv)
    if a.wandb_mode is None:a.wandb_mode='online' if a.mode=='train' else 'disabled'
    positive = ('batch_size', 'token_batch', 'cache_mib', 'threads', 'max_blocks',
                'profile_origins', 'profile_steps', 'epochs', 'patience', 'checkpoint_every', 'log_every')
    if (any(getattr(a,k)<1 for k in positive) or not 0 < a.min_learning_rate <= a.learning_rate
            or a.warmup_updates < 0 or a.validation_every < 1):
        p.error('Resource limits and schedule must be finite positive values')
    if not np.isfinite([a.learning_rate,a.min_learning_rate]).all():p.error('Nonfinite schedule')
    if not np.isfinite(a.lr_restart_decay) or not 0<a.lr_restart_decay<=1:p.error('Restart decay must be in (0,1]')
    if a.validation_every and a.warmup_updates>=a.validation_every:
        p.error('One-time warmup must be shorter than the validation-aligned cycle')
    if not np.isfinite(a.feature_drop_rate) or a.feature_drop_rate != expected_feature_drop:
        p.error(f'Selected design requires feature-drop-rate={expected_feature_drop}')
    if not 0<=a.io_workers<=8 or a.prefetch_mib<1:p.error('Invalid bounded CPU I/O pool')
    if not 0<=a.prepare_workers<=8:p.error('Invalid publication preparation pool')
    if a.student_attention_backend!='default' and not a.cuda_step_replay:
        p.error('Explicit student backend requires CUDA replay')
    if a.cuda_step_replay and (a.device!='cuda' or a.precision!='bf16' or a.mode!='train'):
        p.error('Captured training step requires CUDA/BF16 train mode')
    if a.device!='cuda' and (a.gpu_prefetch or a.optimizer_backend=='fused'):
        p.error('GPU prefetch/fused optimizer require CUDA')
    if a.mode == 'train' and not a.execute:p.error('Training requires --execute')
    if a.mode == 'train' and a.wandb_mode != 'online':p.error('Phase-two training requires online W&B')
    if a.mode == 'profile' and (not a.teacher_checkpoint or not a.teacher_sha256):
        p.error('Profile requires an explicit V4 checkpoint and SHA256')
    if bool(a.teacher_checkpoint) != bool(a.teacher_sha256):p.error('Supply both teacher path and SHA256')
    if a.resume and a.mode != 'train':p.error('Resume is only meaningful for training')
    if a.mode != 'train' and a.wandb_mode != 'disabled':p.error('Audit/profile never creates a W&B run')
    a.output = a.output.resolve()
    if not a.output.is_relative_to(Path('D:/TradingML/runtimes').resolve()):
        p.error('Output must be beneath D:/TradingML/runtimes')
    return a


def bounded_check(units, teacher, proof, args, *, model_type=Forecaster, model_config=Config):
    reports, measured, seen = [], [], 0
    model = model_type(model_config(mask_rate=args.feature_drop_rate,attention_layout=args.attention_layout)).to(args.device) if args.mode == 'profile' else None
    optimizer = optimizer_for(model,args) if model else None
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
                        with autocast(args):
                            predicted,ids=model.training_prediction(tokens,metadata)
                            loss=getattr(model,'training_loss',future_loss)(predicted,target,ids)
                        loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
                        optimizer.step()
                    if args.device == 'cuda':torch.cuda.synchronize()
                    seconds=time.perf_counter()-began
                    measured.append(dict(origins=n*args.profile_steps, seconds=seconds,
                                         samples_per_second=n*args.profile_steps/seconds,
                                         loss=float(loss.detach()), device=args.device,
                                         note='bounded repeated-batch compute, not corpus throughput'))
                    model.eval()
                    with torch.no_grad():
                        with autocast(args):_,predicted=model(tokens,metadata)
                        decoded=DecodedMetrics(args.device)
                        decoded.add(teacher.reconstruction_decoder,predicted,target,
                                    session.reconstruction(small,0,n))
                    report['reconstruction']=decoded.result()
                    model.train()
            reports.append(report);seen+=1
            if seen>=args.max_blocks:break
        if seen>=args.max_blocks:break
    if not reports:raise ValueError('No selected blocks')
    if args.mode=='profile' and not measured:raise ValueError('No eligible profile origins')
    return dict(mode=args.mode, passed=True, blocks=reports, measured=measured,
                scope='bounded selected-block sample; not full-corpus eligibility certification')


def main(argv=None, *, argument_parser=arguments, model_type=Forecaster, model_config=Config, version_sources=()):
    a=argument_parser(argv)
    if (a.output/'STOP').exists():raise ValueError('Human STOP is present; it is never cleared by this launcher')
    if a.output.exists() and not a.resume:
        raise FileExistsError('Use a new output, or explicit --resume with exact contract')
    if a.resume and (not a.output.is_dir() or a.resume.resolve().parent!=a.output):
        raise ValueError('Resume requires a checkpoint in the same existing run output')
    torch.set_num_threads(a.threads)
    random.seed(a.seed);np.random.seed(a.seed);torch.manual_seed(a.seed)
    teacher=proof=None
    selection=None
    if a.mode=='train':
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]))
        require_online_credentials()
        a.teacher_checkpoint,a.teacher_sha256,selection=select_training_teacher(
            a.teacher_checkpoint,a.teacher_sha256)
    if a.teacher_checkpoint:
        teacher,proof=load_teacher(a.teacher_checkpoint,a.teacher_sha256,a.device,
                                  required_epoch=50 if a.mode=='train' else None)
        if selection is not None:proof['selection']=selection
    units,dataset=load_units(a.dataset_root or (),a.dataset_release)
    config=model_config(mask_rate=a.feature_drop_rate,attention_layout=a.attention_layout)
    sources=list(Path(__file__).parent.glob('*.py'))
    sources+=list(version_sources)
    shared=Path(__file__).resolve().parents[1]/'v5'
    sources += [shared/name for name in ('encoder_v3.py','pretraining_v3.py','transforms_v1.py',
                                        'encoder_v4.py','native_validation_v3.py','price_relative_v3.py',
                                        'phase2_ar_shards/storage.py','phase2_ar_shards/contract.py')]
    implementation=digest({str(p.relative_to(Path(__file__).resolve().parents[1])):file_hash(p)
                           for p in sources})
    contract=dict(design=config.manifest(),dataset=dataset,teacher=proof,implementation=implementation,
                  validation=dict(reconstruction=RECONSTRUCTION_CONTRACT,
                                  every_updates=a.validation_every,epoch_end=True,
                                  interval_guard='full validation every N updates and pass end; latent MSE controls patience'),
                  schedule=schedule_manifest(a),
                  tokenizer=dict(tile_batch=a.token_batch,precision=a.precision,
                                 algorithm='fixed-pool-endpoint-tiles-v1'),
                  numerics=dict(torch=str(torch.__version__),cuda=torch.version.cuda,
                                threads=a.threads,matmul=torch.get_float32_matmul_precision(),
                                tf32=bool(torch.backends.cuda.matmul.allow_tf32),
                                deterministic=torch.are_deterministic_algorithms_enabled(),
                                device_name=torch.cuda.get_device_name() if a.device=='cuda' else 'cpu'),
                  optimization=dict(batch=a.batch_size,precision=a.precision,device=a.device,
                      batch_packing=a.pack_batches,io_workers=a.io_workers,prefetch_mib=a.prefetch_mib,
                      gpu_prefetch=a.gpu_prefetch,optimizer_backend=a.optimizer_backend,
                      prepare_workers=a.prepare_workers,cuda_step_replay=a.cuda_step_replay,
                      student_attention_backend=a.student_attention_backend,
                      epochs=a.epochs,peak=a.learning_rate,minimum=a.min_learning_rate,
                      warmup=a.warmup_updates,weight_decay=.01,gradient_clip=1.,seed=a.seed,
                      patience=a.patience,order='root/session/block/eligible-origin chronological',
                      masking=('none; all16x512 context content; loss all512 future coordinates' if config.mask_rate==0 else
                          'fresh batch-shared coordinates removed before encoder; rate from design.model.mask_rate; future target only')))
    # JSON-normalize tuples so checkpoint/reporting identities are platform stable.
    contract=json.loads(json.dumps(contract))
    a.output.mkdir(parents=True,exist_ok=bool(a.resume))
    with lease(a.output):
        if a.mode!='train':
            result=bounded_check(units,teacher,proof,a,model_type=model_type,model_config=model_config)
            result['contract']=contract;write_json(a.output/'result.json',result)
            print(json.dumps(result));return result
        manifest=dict(mode='train',model=contract,dataset=dataset['hash'],seed=a.seed,
                      precision=a.precision,batch_size=a.batch_size,epochs=a.epochs,
                      learning_rate=a.learning_rate,min_learning_rate=a.min_learning_rate,
                      warmup_updates=a.warmup_updates,resume=str(a.resume) if a.resume else None,
                      output=str(a.output),checkpoint=str(a.teacher_checkpoint),
                      phase2_version=config.manifest()['version'],wandb_project=PROJECT,
                      source_revision=a.source_revision or source_revision())
        if not a.resume:write_json(a.output/'manifest.json',manifest)
        reporter=online_reporter(a.output,manifest)
        try:
            model=model_type(config).to(a.device)
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
