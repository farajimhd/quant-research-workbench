"""Bounded paired full-validation timing; no optimizer or W&B writer."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',POLARS_MAX_THREADS='1')
import argparse,time
import torch
from .run_train import arguments
from .model import Forecaster,Config
from .tokenizer import load_teacher
from .train import evaluate
from . import metrics
from ..phase1_v10.metrics import PriceRelative
from ..phase1_v8.runtime import preflight
from ..phase2_v2.data import load_units
from ..phase2_v2.train import load_checkpoint
from ..v5.shards.contract import write_json


class Reporter:
    def update(self,**kwargs):pass


def main():
    parser=argparse.ArgumentParser(add_help=False);parser.add_argument('--checkpoint',required=True)
    own,remaining=parser.parse_known_args()
    a=arguments(remaining)
    if a.mode!='profile':raise ValueError('Only profile mode permitted')
    preflight(a);torch.set_num_threads(a.threads)
    saved=torch.load(own.checkpoint,map_location='cpu',weights_only=False)
    saved=load_checkpoint(own.checkpoint,saved['contract'])
    teacher,proof,_=load_teacher(a.teacher_checkpoint,a.teacher_sha256,a.phase1_calibration,a.device)
    model=Forecaster(Config(**saved['contract']['model'])).to(a.device);model.load_state_dict(saved['model'])
    units,_=load_units(release=a.dataset_release)
    moments=saved['training_metrics']['moments'].to(a.device);mean=moments[2]/moments[4].clamp_min(1)
    new=metrics.HorizonPriceRelative;results=[];reference=None;began=time.perf_counter()
    for mode in ('base','horizon','horizon','base'):
        metrics.HorizonPriceRelative=new if mode=='horizon' else PriceRelative
        start=time.perf_counter();result=evaluate(model,units,teacher,proof['sha256'],a,mean,Reporter(),saved['step'])
        elapsed=time.perf_counter()-start
        scalar={k:result[k] for k in ('loss','balanced_price_mape_pct','activity_log_mae','origins')}
        if reference is None:reference=scalar
        assert all(abs(scalar[k]-reference[k])<1e-10 for k in scalar),(mode,scalar,reference)
        item=dict(mode=mode,wall_seconds=elapsed,**scalar,horizon_keys=sum(k.startswith('horizon/') for k in result))
        results.append(item);print(item,flush=True);write_json(a.output/'comparison.json',results)
        if time.perf_counter()-began>300:raise TimeoutError('Bounded validation comparison deadline')
    metrics.HorizonPriceRelative=new


if __name__=='__main__':main()
