"""Bounded real-tile parity/timing against an immutable pre-optimization V17."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,importlib.util,json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
import numpy as np
import torch
from .data import SessionData
from .resident_features import ResidentFeatures
from .features import CATALOG
from .genome import Individual
from .rule_plan import RulePlan
from .run_search import bind_dataset,source_hash,digest
from . import fused_program
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,configure_caches


def measure(fn,repeats=5):
    fn();torch.cuda.synchronize();samples=[]
    for _ in range(repeats):
        start=perf_counter();fn();torch.cuda.synchronize();samples.append(perf_counter()-start)
    return float(np.median(samples))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline-code',type=Path,required=True)
    p.add_argument('--campaign',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();out=require_runtime(args.output);configure_caches(out/'cache');torch.set_num_threads(1)
    saved=json.loads((args.campaign/'checkpoint.json').read_text(encoding='utf-8-sig'))
    assert digest(saved['state'])==saved['state_sha256']
    spec=importlib.util.spec_from_file_location('research.vectorized_backtest.v17._compute_reference',args.baseline_code/'research/vectorized_backtest/v17/fused_program.py')
    reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)
    members=[Individual.restore(v) for v in saved['state']['population'][:512]]
    timings={};checks=[]
    for role in ('training','validation'):
        dataset=bind_dataset(args.campaign/(role+'.json'),role);day=dataset['days'][0]
        data=SessionData(Path(dataset['inputs'])/day,Path(dataset['history'])/day,
                         feature_cache=Path(dataset['feature_cache'])/day if dataset.get('feature_cache') else None,
                         indicator_root=Path(dataset['indicator_root'])/day)
        resident=ResidentFeatures(data);resident.activate('cuda');a=resident.tensors
        begin=4000;end=6119;listings=torch.tensor(np.argsort(-data.host_tensors['observed'][begin:end].sum(0).numpy())[:4],device='cuda')
        rows=a['source'][begin:end].index_select(1,listings).T
        ids=a['ids'][begin:end].index_select(1,listings).T.to(torch.int64);clock=a['clocks'][begin:end][None]
        # Include real missing clocks, all families, and original column order.
        all_columns=list(range(len(CATALOG)))
        expected,mask=resident._values_reference(rows,ids,clock,all_columns)
        actual,known=resident._values(rows,ids,clock,all_columns)
        torch.testing.assert_close(actual,expected,rtol=0,atol=0);assert torch.equal(mask,known)
        checks.append(role+' real 4x2119 all-channel projection bit-exact')
        gather_times={name:measure(lambda fn=fn:fn(rows,ids,clock,all_columns)) for name,fn in [('baseline',resident._values_reference),('optimized',resident._values)]}
        outputs={};rule_times={};details={}
        for name,cls in [('baseline',reference.FusedPrograms),('optimized',fused_program.FusedPrograms)]:
            with patch.object(fused_program,'FusedPrograms',cls):plan=RulePlan(members,'cuda',maximum_capture_gib=2.)
            try:
                inputs=[]
                for g in plan.groups:
                    x=actual[...,g['columns']].contiguous();m=known[...,g['columns']].contiguous()
                    if g['sparse']:
                        x=x.reshape(1,-1,x.shape[-1])[:,:2048].contiguous()
                        m=m.reshape(1,-1,m.shape[-1])[:,:2048].contiguous()
                    inputs.append((x,m))
                def evaluate():
                    return [plan.execute(i,x,m) for i,(x,m) in enumerate(inputs)]
                outputs[name]=[v.clone() for v in evaluate()]
                rule_times[name]=measure(evaluate,3)
                details[name]=dict(capture_bytes=plan.capture_bytes,builds=plan.builds,evictions=plan.evictions)
            finally:plan.close();torch.cuda.empty_cache()
        assert all(torch.equal(a,b) for a,b in zip(outputs['baseline'],outputs['optimized']))
        checks.append(role+' actual checkpoint rules bit-exact, original numeric mean groups retained')
        timings[role]=dict(day=day,feature_seconds=gather_times,rule_seconds=rule_times,
                           feature_ratio=gather_times['optimized']/gather_times['baseline'],
                           rule_ratio=rule_times['optimized']/rule_times['baseline'],details=details)
        resident.deactivate();data.close();del actual,expected,known,mask,inputs,outputs,resident,data,a;torch.cuda.empty_cache()
    result=dict(version='v17-compute-parity-v1',status='passed',source_sha256=source_hash(),
                baseline_source_sha256=saved['contract']['source_sha256'],checkpoint_state_sha256=saved['state_sha256'],
                timings=timings,checks=checks,scope='matched 512-candidate real feature tiles, sparse 2048-point and dense 4x2119 rules; not full-generation speed')
    write_json(out/'qualification.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':main()
