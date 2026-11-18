"""Immutable workstation admission and full Phase1 MAE entry point."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONUNBUFFERED='1')
import argparse,json,time,random
from pathlib import Path
import numpy as np
import torch
from .model import Config,Autoencoder
from .data import read_calibration
from ..phase1_v8.runtime import preflight
from ..v5.shards.phase1_contract import write_json,file_hash,digest


def make_model(a):
    return Autoencoder(Config(width=a.width,layers=a.layers,embedding=a.embedding,context=a.context,precision=a.precision),
                       read_calibration(a.calibration)['parameters']).to(a.device)


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('engineering','compute','stream','validation-profile','train'))
    p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--calibration',type=Path,required=True)
    p.add_argument('--data',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset'))
    p.add_argument('--split',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v6-cohort-balanced-v4/split.json'))
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda');p.add_argument('--precision',choices=('bf16','fp32'),default='bf16')
    p.add_argument('--width',type=int,default=384);p.add_argument('--layers',type=int,default=6)
    p.add_argument('--embedding',type=int,default=768);p.add_argument('--context',type=int,default=192)
    p.add_argument('--batch-size',type=int,default=2048);p.add_argument('--batches',nargs='+',type=int,default=[512,1024,2048,4096,8192])
    p.add_argument('--workers',type=int,default=16);p.add_argument('--prefetch',type=int,default=4)
    p.add_argument('--shard-prefetch',type=int,default=16);p.add_argument('--shard-prefetch-mib',type=int,default=64)
    p.add_argument('--validation-workers',type=int,default=-1);p.add_argument('--validation-batch-size',type=int,default=0)
    p.add_argument('--cuda-graph',action=argparse.BooleanOptionalAction,default=True)
    p.add_argument('--steps',type=int,default=40);p.add_argument('--tiny-steps',type=int,default=800)
    p.add_argument('--epochs',type=int,default=100);p.add_argument('--learning-rate',type=float,default=1e-3)
    p.add_argument('--min-learning-rate',type=float,default=1e-5);p.add_argument('--seed',type=int,default=17)
    p.add_argument('--checkpoint-every',type=int,default=1000);p.add_argument('--diagnostic-every',type=int,default=5000)
    p.add_argument('--max-updates',type=int,default=0);p.add_argument('--resume',action='store_true')
    p.add_argument('--wandb-mode',choices=('online','disabled'),default='online');a=p.parse_args()
    if not 0<=a.workers<=32 or not -1<=a.validation_workers<=32 or not 1<=a.prefetch<=4:raise ValueError('Loader bounds')
    if not 1<=a.shard_prefetch<=64 or not 1<=a.shard_prefetch_mib<=1024:raise ValueError('Prefetch bounds')
    if min(a.batch_size,a.steps,a.epochs,a.layers,a.checkpoint_every,a.diagnostic_every)<=0:raise ValueError('Positive sizes required')
    if a.width%32 or not 0<a.context<a.embedding or not 0<a.min_learning_rate<=a.learning_rate:raise ValueError('Invalid architecture/schedule')
    if a.device=='cpu' and a.mode!='engineering':raise ValueError('Heavy execution belongs on workstation')
    a.preparation='raw_gpu';preflight(a)
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest');torch.manual_seed(a.seed);np.random.seed(a.seed);random.seed(a.seed)
    aux=json.loads((a.data/'manifest.json').read_text())
    if digest({k:v for k,v in aux.items() if k!='hash'})!=aux['hash']:raise ValueError('Auxiliary seal changed')
    for name in ('train.npy','tiny.npy','development.npy'):
        if file_hash(a.data/name)!=aux['files'][name]:raise ValueError('Auxiliary payload changed')
    calibration=read_calibration(a.calibration)
    if calibration['training_dataset_hash']!=aux['hash']:raise ValueError('Calibration auxiliary provenance mismatch')
    write_json(a.output/('resume-arguments.json' if a.resume else 'arguments.json'),{k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()})
    state=dict(state='running',started=time.time(),mode=a.mode,epochs=a.epochs)
    def status(**kw):state.update(kw,updated=time.time());write_json(a.output/'status.json',state)
    from ..phase2_v2.train import lease
    try:
        with lease(a.output):
            status(stage='verifying certified authority')
            if a.mode in ('engineering','compute'):
                from . import profile
                result=getattr(profile,a.mode)(a,status)
            else:
                from ..v5.data_v3 import load_split
                from ..v5.loader_index_v1 import attach
                manifest=load_split(a.split)
                if manifest['manifest_hash']!=calibration['source_split_hash']:raise ValueError('Full split differs from calibration authority')
                manifest=attach(manifest,Path(manifest['dataset']));manifest['splits']={}
                write_json(a.output/'authority.json',dict(split_hash=manifest['manifest_hash'],calibration_hash=calibration['hash'],
                    counts=manifest['counts'],policy='All certified partition windows, all worker tails; no sample cap in training or full validation.'))
                status(epoch_total=manifest['counts']['train'],validation_total=manifest['counts']['validation'])
                if a.mode=='stream':
                    from .profile import stream
                    result=stream(a,manifest,status)
                elif a.mode=='validation-profile':
                    from .train import evaluate
                    result=evaluate(make_model(a),manifest,a,status,limit=a.steps)
                    write_json(a.output/'validation-profile.json',result)
                else:
                    from .train import train
                    result=train(a,manifest,status)
            status(state=result if isinstance(result,str) else 'complete',stage=result if isinstance(result,str) else 'qualification complete')
            print(json.dumps(result if isinstance(result,str) else {k:v for k,v in result.items() if isinstance(v,(str,int,float,bool))}),flush=True)
    except BaseException as e:status(state='failed',stage='failed',error=f'{type(e).__name__}: {e}');raise


if __name__=='__main__':main()
