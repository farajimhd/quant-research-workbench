"""Finite CPU comparison of initializers. Does not launch or contact optimizers."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','POLARS_MAX_THREADS'):os.environ.setdefault(key,'1')
import argparse,json,hashlib
from collections import Counter
from pathlib import Path
from time import perf_counter
import numpy as np
import psutil
import torch
from .calibration import load_probe,observe
from .sampling_probe import build
from .initial_population import initialize
from .genome import STAGES
from .sampling_report import coverage
from research.vectorized_backtest.v30.initial_population import initialize as baseline_initialize
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime

def quantiles(values):return np.quantile(values,[0,.25,.5,.75,1]).tolist()

def measure(members,probe):
    started=perf_counter();stages={};signatures=[hashlib.sha256() for _ in members]
    for stage in STAGES:
        rates=[];switches=[];saturated=total=0;output_sat=output_count=0
        for i,m in enumerate(members):
            values,valid,sat,count=observe(m.networks[stage],probe,stage)
            mask=valid & (probe['eligible'] if stage=='entry' else True)
            fire=mask & (values>np.float32(m.networks[stage].threshold))
            rates.append(float(fire.sum()/max(1,mask.sum())))
            adjacent=mask[:,1:]&mask[:,:-1]
            switches.append(float(((fire[:,1:]!=fire[:,:-1])&adjacent).sum()/max(1,adjacent.sum())))
            saturated+=sat;total+=count;output_sat+=int(((np.abs(values)>.99)&mask).sum());output_count+=int(mask.sum())
            signatures[i].update(np.packbits(fire).tobytes())
        rates=np.asarray(rates)
        stages[stage]=dict(rate_quantiles=quantiles(rates),never=int((rates==0).sum()),always=int((rates==1).sum()),
            near_silent=int((rates<.01).sum()),near_persistent=int((rates>.99).sum()),
            intermittent=int(((rates>=.01)&(rates<=.99)).sum()),
            switch_rate_quantiles=quantiles(switches),valid_neuron_saturation=saturated/total,
            valid_output_saturation=output_sat/max(1,output_count))
    return dict(seconds=perf_counter()-started,actions=stages,distinct_response_patterns=len({h.hexdigest() for h in signatures}))

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--training',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--day',default='2026-08-04');p.add_argument('--population',type=int,default=1024);p.add_argument('--seed',type=int,default=2020)
    p.add_argument('--steps',type=int,default=16);a=p.parse_args(argv)
    if not 10<=a.population<=1024:p.error('Bounded comparison population must be 10..1024')
    out=require_runtime(a.output)
    if (out/'comparison.json').exists():raise ValueError('Use a new comparison output directory')
    source_files={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(Path(__file__).parent.glob('*.py'))}
    torch.set_num_threads(1);started=perf_counter();probe_times={}
    for label,starts in [('calibration',(1500,7500,13500)),('heldout_training',(3000,9000,16500))]:
        began=perf_counter();values=build(a.training,a.day,starts,a.steps);np.savez(out/(label+'.npz'),**values)
        probe_times[label]=perf_counter()-began;print(label+' probe '+str(round(probe_times[label],3))+'s',flush=True)
    probes={k:load_probe(out/(k+'.npz'),a.training) for k in probe_times}
    cases={}
    for version,init in [('v30',baseline_initialize),('v33',initialize)]:
        began=perf_counter();rng=np.random.default_rng(a.seed)
        members,report=init(rng,a.population,**({'probe':probes['calibration']} if version=='v33' else {}))
        elapsed=perf_counter()-began;print(version+' initialization '+str(round(elapsed,3))+'s',flush=True)
        payload=[m.payload() for m in members]
        (out/(version+'-population.json')).write_text(json.dumps(payload,separators=(',',':')))
        (out/(version+'-initialization.json')).write_text(json.dumps(report,indent=2))
        cases[version]=dict(initialization_seconds=elapsed,coverage=coverage(members),
            population_sha256=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            policies={k:quantiles([getattr(m.policy,k) for m in members]) for k in members[0].policy.__dataclass_fields__},
            probes={label:measure(members,probe) for label,probe in probes.items()})
        print(version+' response measurement complete',flush=True)
    result=dict(population=a.population,seed=a.seed,training_sha256=hashlib.sha256(a.training.read_bytes()).hexdigest(),
        probe_build_seconds=probe_times,probes={k:v['metadata'] for k,v in probes.items()},cases=cases,
        total_seconds=perf_counter()-started,process_rss_gib=psutil.Process().memory_info().rss/2**30,
        process_peak_rss_gib=getattr(psutil.Process().memory_info(),'peak_wset',psutil.Process().memory_info().rss)/2**30,
        validation_opened=False,gpu_used=False,optimizer_launched=False,
        scope='Activation behavior on disjoint training windows and hypothetical held states; no trade/fee/profit claims',
        source_files=source_files)
    (out/'comparison.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:result[k] for k in ('total_seconds','process_rss_gib','optimizer_launched')},indent=2),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
