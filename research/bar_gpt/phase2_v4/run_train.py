"""Versioned V11-encoder forecasting pretraining; immutable source and full release."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',POLARS_MAX_THREADS='1',PYTHONUNBUFFERED='1')
import argparse,json,random,sys,time
from dataclasses import asdict
from pathlib import Path
import numpy as np
import torch
from ..phase2_v2.run_train import arguments as shared_arguments
from .data import load_units
from ..phase2_v2.train import lease
from .schedule import schedule_manifest
from ..phase1_v8.runtime import preflight
from ..v5.shards.contract import file_hash,write_json
from ..v5.reporting_v1 import Reporter
from research.mlops.env import discover_env_files,load_env_files
from .tokenizer import load_teacher
from .model import Forecaster,Config


def arguments(argv=None):
    p=argparse.ArgumentParser(add_help=False);p.add_argument('--phase1-calibration',type=Path,required=True)
    p.add_argument('--layers',type=int,default=2)
    p.add_argument('--forecast-audit',type=Path)
    a,remaining=p.parse_known_args(sys.argv[1:] if argv is None else argv)
    defaults={'--epochs':'1','--learning-rate':'0.00003','--min-learning-rate':'0.000003','--validation-every':'5000','--warmup-updates':'1000','--patience':'24','--seed':'17'}
    for k,v in defaults.items():
        if not any(x==k or x.startswith(k+'=') for x in remaining):remaining.extend((k,v))
    args=shared_arguments(remaining,expected_feature_drop=0.)
    if args.mode not in ('profile','train'):raise ValueError('Use explicit profile or train mode; no implicit training')
    args.phase1_calibration=a.phase1_calibration;args.layers=a.layers
    args.forecast_audit=a.forecast_audit
    if args.layers not in (1,2):raise ValueError('Qualified one/two-layer comparison only')
    if not args.teacher_checkpoint or not args.teacher_sha256:raise ValueError('Explicit completed phase1 checkpoint required')
    return args


def main(argv=None):
    a=arguments(argv);preflight(a);torch.set_num_threads(a.threads)
    random.seed(a.seed);np.random.seed(a.seed);torch.manual_seed(a.seed)
    teacher,proof,scale=load_teacher(a.teacher_checkpoint,a.teacher_sha256,a.phase1_calibration,a.device)
    model=Forecaster(Config(layers=a.layers)).to(a.device);model.decoder.initialize_phase1(torch.load(a.teacher_checkpoint,map_location='cpu',weights_only=False)['model'])
    if a.mode=='profile':
        from .profile import profile
        return profile(model,teacher,proof,a)
    units,dataset=load_units(a.dataset_root or (),a.dataset_release)
    contract=json.loads(json.dumps(dict(version='bargpt-phase2-v4',model=asdict(model.config),teacher=proof,dataset=dataset,
        source=a.source_revision,schedule=schedule_manifest(a),head_initialization=proof['sha256'],
        numerics=dict(torch=str(torch.__version__),precision=a.precision,token_batch=a.token_batch,batch=a.batch_size,prepare_workers=a.prepare_workers,
            io_workers=a.io_workers,prefetch_mib=a.prefetch_mib,cache_mib=a.cache_mib,gpu_prefetch=a.gpu_prefetch,cuda_step_replay=a.cuda_step_replay,threads=a.threads,seed=a.seed),
        decoder_dependency='256-dimensional learned context embedding only; no true or raw reference bypass',
        objective='Equal price/activity family source-valid SmoothL1; price1000*log error, activitylog1p error; batch-window-weighted validation',
        export='encoder only for256-dimensional representation; optional decoder for64x18 raw forecasts')))

    from .resume_metrics import preserve_contract,upload_audit
    contract=preserve_contract(a,contract)
    manifest=dict(model=contract,phase2_version='bargpt-phase2-v4',source_revision=a.source_revision,output=str(a.output),resume=str(a.resume) if a.resume else None,
        parameters=sum(p.numel() for p in model.parameters()),encoder_parameters=sum(p.numel() for p in model.encoder.parameters()),
        arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()})
    if not a.resume:write_json(a.output/'manifest.json',manifest)
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]))
    os.environ.setdefault('WANDB_ENTITY','mehdifaraji')
    with lease(a.output):
        reporter=Reporter(a.output,manifest,mode='online',project='bargpt-phase2')
        if reporter.run is None:raise RuntimeError('Online W&B is mandatory')
        reporter.run.define_metric('train/loss',step_metric='global_step',summary='min',hidden=False)
        reporter.run.define_metric('validation_horizon/*',step_metric='global_step',hidden=False)
        reporter.update(stage='preparing certified publications',epoch=1,epochs=a.epochs,step=0)
        try:
            upload_audit(a,contract,reporter)
            from .train import train
            state=train(model,teacher,proof['sha256'],units,contract,a,reporter)
            # Export final durable checkpoint, not possibly uncheckpointed memory.
            saved=torch.load(a.output/'checkpoint_last.pt',map_location='cpu',weights_only=False)
            export=dict(config=asdict(model.config),contract=contract,step=saved['step'],checkpoint_sha256=file_hash(a.output/'checkpoint_last.pt'))
            torch.save(dict(export,state={k.removeprefix('encoder.'):v for k,v in saved['model'].items() if k.startswith('encoder.')}),a.output/'context-encoder.pt')
            torch.save(dict(export,state={k.removeprefix('decoder.'):v for k,v in saved['model'].items() if k.startswith('decoder.')}),a.output/'forecast-decoder.pt')
            write_json(a.output/'exports.json',{name:file_hash(a.output/name) for name in ('context-encoder.pt','forecast-decoder.pt')})
            reporter.finish(state);return state
        except BaseException as exc:reporter.finish('failed',repr(exc));raise


if __name__=='__main__':main()

