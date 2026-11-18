"""Explicit full-training entry point; finite campaign never invokes it implicitly."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONUNBUFFERED='1')
import argparse,json,random,time
from pathlib import Path
import numpy as np
import torch
from .model import Autoencoder
from .config import Config
from .data import verify
from ..phase1_v8.runtime import preflight
from ..phase2_v2.train import load_checkpoint,lease
from ..v5.data_v3 import load_split
from ..v5.loader_index_v1 import attach
from ..v5.shards.phase1_contract import file_hash,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--calibration',type=Path,required=True)
    p.add_argument('--data',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset'))
    p.add_argument('--split',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v6-cohort-balanced-v4/split.json'))
    p.add_argument('--batch-size',type=int,default=1024);p.add_argument('--workers',type=int,default=16);p.add_argument('--prefetch',type=int,default=4)
    p.add_argument('--shard-prefetch',type=int,default=16);p.add_argument('--shard-prefetch-mib',type=int,default=64)
    p.add_argument('--validation-batch-size',type=int,default=2048);p.add_argument('--validation-workers',type=int,default=16)
    p.add_argument('--validation-graphs',action='store_true')
    p.add_argument('--epochs',type=int,default=100);p.add_argument('--learning-rate',type=float,default=1e-3);p.add_argument('--min-learning-rate',type=float,default=1e-5)
    p.add_argument('--seed',type=int,default=17);p.add_argument('--checkpoint-every',type=int,default=1000);p.add_argument('--diagnostic-every',type=int,default=5000)
    p.add_argument('--max-updates',type=int,default=0);p.add_argument('--retain-optimizer',action='store_true');p.add_argument('--resume',action='store_true');p.add_argument('--resume-contract-source')
    a=p.parse_args();a.device='cuda';a.preparation='raw_gpu'
    if not 0<=a.workers<=32 or not 0<=a.validation_workers<=32 or not 1<=a.prefetch<=4:raise ValueError('Loader bounds')
    if min(a.batch_size,a.epochs,a.checkpoint_every,a.diagnostic_every)<=0 or not 0<a.min_learning_rate<=a.learning_rate:raise ValueError('Invalid schedule')
    if a.resume_contract_source and not a.resume:raise ValueError('Engineering contract source requires resume')
    preflight(a);torch.set_num_threads(4);torch.manual_seed(a.seed);np.random.seed(a.seed);random.seed(a.seed)
    frozen=verify(a.data);calibration=json.loads(a.calibration.read_text());parent=torch.load(a.checkpoint,map_location='cpu',weights_only=False);parent=load_checkpoint(a.checkpoint,parent['contract'])
    if parent['contract']['data_hash']!=frozen['hash'] or parent['contract']['calibration_sha256']!=file_hash(a.calibration):raise ValueError('Parent data/calibration differs')
    manifest=load_split(a.split)
    if manifest['manifest_hash']!=frozen['source_split_hash']:raise ValueError('Full split differs from qualified source')
    manifest=attach(manifest,Path(manifest['dataset']));manifest['splits']={}
    model=Autoencoder(Config(**parent['contract']['config']),calibration['parameters']).cuda();model.load_state_dict(parent['model'])
    state=dict(state='running',started=time.time(),epochs=a.epochs,epoch_total=manifest['counts']['train'],validation_total=manifest['counts']['validation'])
    def status(**kw):state.update(kw,updated=time.time());write_json(a.output/'status.json',state)
    write_json(a.output/'authority.json',dict(split_hash=manifest['manifest_hash'],counts=manifest['counts'],calibration_sha256=file_hash(a.calibration),parent_checkpoint_sha256=file_hash(a.checkpoint),
        scope='Every certified training window and every certified validation window; no population cap except explicit bounded --max-updates qualification.'))
    try:
        from .full_train import train
        with lease(a.output):result=train(model,parent,a,manifest,status)
        status(state=result,stage=result);print(result,flush=True)
    except BaseException as exc:status(state='failed',stage='failed',error=repr(exc));raise


if __name__=='__main__':main()
