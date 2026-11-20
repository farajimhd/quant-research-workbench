"""Paired existing-loader profiling; no optimizer, census scan, or W&B writer."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',POLARS_MAX_THREADS='1')
import argparse,json,time
from itertools import islice
from pathlib import Path
import numpy as np
import torch
from .run_train import arguments
from .data import load_units,batches
from .tokenizer import load_teacher
from .loader_census import TAG,LoaderCensus
from ..phase1_v8.runtime import preflight
from ..phase2_v2.train import load_checkpoint
from ..phase2_v2.device_pipeline import device_batches
from ..v5.shards.contract import write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-revision',required=True);p.add_argument('--units',type=int,default=8)
    own=p.parse_args()
    if not 1<=own.units<=32:raise ValueError('Bounded profile units required')
    command=json.loads(own.run.with_name(own.run.name+'-launcher').joinpath('launch.json').read_text())['command']
    argv=command[command.index('research.bar_gpt.phase2_v4.run_train')+1:]
    for flag in ('--resume','--forecast-audit'):
        if flag in argv:
            i=argv.index(flag);del argv[i:i+2]
    if '--cuda-step-replay' in argv:argv.remove('--cuda-step-replay')
    for flag,value in (('--mode','profile'),('--output',str(own.output)),('--source-revision',own.source_revision)):
        argv[argv.index(flag)+1]=value
    a=arguments(argv);preflight(a);torch.set_num_threads(a.threads)
    saved=torch.load(own.run/'checkpoint_last.pt',map_location='cpu',weights_only=False)
    saved=load_checkpoint(own.run/'checkpoint_last.pt',saved['contract'])
    teacher,proof,_=load_teacher(a.teacher_checkpoint,a.teacher_sha256,a.phase1_calibration,a.device)
    all_units,_=load_units(release=a.dataset_release)
    units=[u for _,u in islice(all_units.iter_from(0,saved['cursor'][0]),own.units)]
    records=[];reference=None
    for enabled in (False,True,True,False):
        a.loader_census=enabled;count=0;first=None;ledger=LoaderCensus(0,0,(0,0,0));step=0
        started=time.perf_counter()
        source=device_batches(batches(units,0,teacher,proof['sha256'],a),a.device,a.gpu_prefetch)
        try:
            for x,ages,target,cursor,_,_ in source:
                count+=len(x);step+=1
                if enabled:ledger.commit(target[TAG],step,0,cursor)
                if first is None:
                    first=(x[:8].cpu().clone(),ages[:8].cpu().clone(),target['logs'][:8].cpu().clone())
                    if enabled:
                        raw=target['raw'][:8,0,[3,9,15]].cpu().double().numpy()
                        last=target['last_raw'][:8,[3,9,15]].cpu().double().numpy()
                        valid=(target['valid'][:8,0,[3,9,15]]&target['last_valid'][:8,[3,9,15]]).cpu().numpy()
                        from .move_contract import EDGES_PCT
                        r=100*(raw/np.where(valid,last,1)-1)
                        expected=np.where(valid,np.where(r==0,0,1+np.searchsorted(EDGES_PCT,abs(r))+5*(r>0)),-1)
                        np.testing.assert_array_equal(target[TAG][:8,:3],expected)
        finally:source.close()
        torch.cuda.synchronize();seconds=time.perf_counter()-started
        if reference is None:reference=(count,first)
        else:
            assert count==reference[0]
            for value,expected in zip(first,reference[1]):torch.testing.assert_close(value,expected,rtol=0,atol=0)
        if enabled:assert ledger.origins==count
        row=dict(enabled=enabled,origins=count,steps=step,seconds=seconds,origins_per_second=count/seconds)
        records.append(row);write_json(a.output/'profile.json',dict(source=own.source_revision,passed=True,records=records))
        print(row,flush=True)


if __name__=='__main__':main()
