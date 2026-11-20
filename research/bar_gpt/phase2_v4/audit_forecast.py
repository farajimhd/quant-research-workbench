"""One full validation, no optimizer and no W&B writer. Run only on an idle GPU."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',POLARS_MAX_THREADS='1')
import argparse,json,time
from pathlib import Path
import torch
from .run_train import arguments
from .data import load_units
from .model import Forecaster,Config
from .tokenizer import load_teacher
from .train import evaluate
from .forecast_metrics import ForecastMetrics
from ..phase1_v8.runtime import preflight
from ..phase2_v2.train import load_checkpoint
from ..v5.shards.contract import write_json,file_hash,digest


class Reporter:
    def __init__(self,output):self.output=output;self.last=0
    def update(self,**values):
        if time.monotonic()-self.last>5:
            write_json(self.output/'progress.json',values);print(values,flush=True);self.last=time.monotonic()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source-revision',required=True)
    parser.add_argument('--compare-overhead',action='store_true')
    own=parser.parse_args()
    launch=json.loads(own.run.with_name(own.run.name+'-launcher').joinpath('launch.json').read_text())
    argv=launch['command'];argv=argv[argv.index('research.bar_gpt.phase2_v4.run_train')+1:]
    for flag in ('--resume','--forecast-audit'):
        if flag in argv:
            i=argv.index(flag);del argv[i:i+2]
    if '--cuda-step-replay' in argv:argv.remove('--cuda-step-replay')
    for flag,value in (('--mode','profile'),('--output',str(own.output)),('--source-revision',own.source_revision)):
        argv[argv.index(flag)+1]=value
    a=arguments(argv);preflight(a);torch.set_num_threads(a.threads)
    checkpoint=own.run/'checkpoint_last.pt';saved=torch.load(checkpoint,map_location='cpu',weights_only=False)
    saved=load_checkpoint(checkpoint,saved['contract'])
    teacher,proof,_=load_teacher(a.teacher_checkpoint,a.teacher_sha256,a.phase1_calibration,a.device)
    model=Forecaster(Config(**saved['contract']['model'])).to(a.device);model.load_state_dict(saved['model'])
    units,dataset=load_units(release=a.dataset_release)
    if digest(dataset)!=digest(saved['contract']['dataset']):raise ValueError('Dataset changed')
    moments=saved['training_metrics']['moments'].to(a.device);mean=moments[2]/moments[4].clamp_min(1)
    from . import metrics as metric_module
    class DisabledForecast:
        def __init__(self,*args):pass
        def add(self,*args):pass
        def result(self):return {}
    timings=[];result=None;reference=None
    for mode in (('base','extended','base') if own.compare_overhead else ('extended',)):
        metric_module.ForecastMetrics=ForecastMetrics if mode=='extended' else DisabledForecast
        began=time.monotonic()
        measured=evaluate(model,units,teacher,proof['sha256'],a,mean,Reporter(a.output),saved['step'])
        elapsed=time.monotonic()-began
        if measured is None:raise RuntimeError('Audit interrupted, not complete')
        if reference is None:reference=measured['loss']
        if measured['loss']!=reference:raise ValueError('Evaluation loss parity failed')
        timings.append(dict(mode=mode,seconds=elapsed,origins=measured['origins']))
        if mode=='extended':result=measured;extended_seconds=elapsed
        write_json(a.output/'timings.json',timings)
    metric_module.ForecastMetrics=ForecastMetrics
    if result is None:raise RuntimeError('Audit interrupted, not complete')
    if not saved['training_metrics']['count']:
        # End-of-validation checkpoints reset interval moments. Zero is not a
        # valid training-population control; retain the existing historical row.
        result={k:v for k,v in result.items() if not k.startswith(('control/training_mean/','skill/training_mean/'))}
    receipt=dict(state='complete',version=ForecastMetrics.VERSION,source=a.source_revision,
        checkpoint=str(checkpoint),checkpoint_sha256=file_hash(checkpoint),contract_sha256=digest(saved['contract']),
        dataset_sha256=digest(dataset),step=saved['step'],seconds=extended_seconds,timings=timings,metrics=result)
    write_json(a.output/'audit.json',receipt)
    print(json.dumps({k:v for k,v in receipt.items() if k!='metrics'}),flush=True)


if __name__=='__main__':main()
