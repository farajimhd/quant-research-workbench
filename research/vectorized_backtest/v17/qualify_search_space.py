"""Bounded V14/V17 rule timing and exact rule/accounting regression; no market data."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
from time import perf_counter
import numpy as np
import torch
from .genome import sample,effective_rules
from .features import CATALOG
from .rule_plan import RulePlan
from .lineage import Pedigree,next_population
from .run_search import source_hash
from research.vectorized_backtest.v15.genome import sample as old_sample
from research.vectorized_backtest.v15.rule_plan import RulePlan as OldPlan
from .program import TorchPrograms
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,configure_caches


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--device',default='cuda')
    args=p.parse_args();root=require_runtime(args.output);configure_caches(root/'cache');torch.set_num_threads(1)
    sync=lambda:torch.cuda.synchronize() if args.device=='cuda' else None
    populations={v:fn(np.random.default_rng(152237),1024) for v,fn in [('v15',old_sample),('v17',sample)]}
    generator=torch.Generator().manual_seed(15)
    x=torch.rand((4,256,len(CATALOG)),generator=generator)
    valid=torch.rand(x.shape,generator=generator)>.02
    for i,f in enumerate(CATALOG):
        if f.unit=='bool':x[...,i]=(x[...,i]>.5).float()
        elif f.unit=='price':x[...,i]=x[...,i]*20+.01
        elif f.unit=='log_price':x[...,i]=x[...,i]*3
        elif 'average_move.' in f.name and f.unit=='ratio':x[...,i]=x[...,i]*.01-1
    x=x.to(args.device);valid=valid.to(args.device);timings={};checks=[]
    for version,cls in [('v15',OldPlan),('v17',RulePlan)]:
        start=perf_counter();plan=cls(populations[version][:512],args.device,maximum_capture_gib=2.)
        try:
            inputs=[(x[...,g['columns']].contiguous(),valid[...,g['columns']].contiguous()) for g in plan.groups]
            sync();construct=perf_counter()-start;start=perf_counter()
            for i,(values,known) in enumerate(inputs):
                actual=plan.execute(i,values,known)
                g=plan.groups[i]
                reference=TorchPrograms(g['program'].programs,[CATALOG[j] for j in g['columns']],args.device)
                expected,mask=reference(values,known)
                torch.testing.assert_close(actual,(expected!=0)&mask,rtol=0,atol=0)
            sync();cold=perf_counter()-start
            samples=[]
            for repeat in range(7):
                start=perf_counter()
                for i,(values,known) in enumerate(inputs):plan.execute(i,values,known)
                sync();samples.append(perf_counter()-start)
            timings[version]=dict(plan_seconds=construct,cold_capture_check_seconds=cold,
                warm_rule_seconds=float(np.median(samples)),groups=len(plan.groups),capture_bytes=plan.capture_bytes)
            checks.append(version+' captured rules equal eager outputs and masks')
        finally:plan.close()
    # Same fresh V17 population on the unchanged V15 evaluator: all policy/age
    # differences are inputs, and every financial metric must be exactly equal.
    from research.vectorized_backtest.v7.qualify import synthetic
    from research.vectorized_backtest.v15.evaluator import replay_cohort as baseline
    from .evaluator import replay_cohort,Execution
    from .qualify_synthetic import exact_accounting
    members=populations['v17'][:64]
    gates=torch.tensor([1,4,8,16,2,0],dtype=torch.uint8,device=args.device)[None,:,None].expand(64,-1,-1).clone()
    execution=Execution(fee_per_share=.005,minimum_order_fee=1)
    for backend in ('eager','compile') if args.device=='cuda' else ('eager',):
        left=baseline([synthetic(args.device)],members,[gates],execution=execution,backend=backend)[0]
        right=replay_cohort([synthetic(args.device)],members,[gates],execution=execution,backend=backend)[0]
        exact_accounting({k:v for k,v in left.items() if isinstance(v,torch.Tensor)},right)
        assert left['episode_profits']==right['episode_profits']
        checks.append(backend+' same-backend accounting exactly matches V15')
    rng=np.random.default_rng(15);pop=populations['v17'];pedigree=Pedigree();ids=pedigree.birth(pop,1,[(-1,-1)]*1024,['fresh']*1024)
    values=rng.normal(size=(30,1024));start=perf_counter()
    _,_,selection=next_population(rng,pop,ids,pedigree,[],2,session_values=values,session_eligible=np.ones_like(values,dtype=bool),global_values=values.sum(0))
    selection_seconds=perf_counter()-start
    result=dict(status='passed',source_sha256=source_hash(),device=args.device,timings=timings,
        warm_rule_ratio_v17_v15=timings['v17']['warm_rule_seconds']/timings['v15']['warm_rule_seconds'],
        selection_seconds=selection_seconds,selection=selection,checks=checks,
        scope='bounded synthetic 512-candidate rules and 64-candidate accounting; not full generation speed')
    write_json(root/'qualification.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':main()
