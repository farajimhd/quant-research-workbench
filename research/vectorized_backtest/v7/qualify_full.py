"""Full-clock/all-identity training qualification against an independent ledger."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
import numpy as np
import torch
from types import SimpleNamespace
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,configure_caches,file_hash
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run
from research.vectorized_backtest.v6.torch_backtest.run_structure import training_days
from .data import SessionData
from .evaluator import replay_cohort,PopulationPrograms,Execution
from .genome import sample
from .qualify import member
from .reference import replay_reference
from .run_search import source_hash

def check(expected,actual):
    errors={}
    for name,value in actual.items():
        value=value.cpu().numpy();np.testing.assert_allclose(value,expected[name],rtol=1e-9,atol=1e-7,equal_nan=True)
        errors[name]=float(np.max(np.abs(value.astype(float)-expected[name].astype(float))))
    return errors

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','history','feature-cache','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--days',nargs='+',required=True)
    a=p.parse_args();days=training_days(a.inputs)
    if any(day not in days for day in a.days):raise ValueError('Only certified training days are permitted')
    root=require_runtime(a.output);configure_caches(root/'cache');torch.set_num_threads(1)
    members=[member() for _ in range(6)]+sample(np.random.default_rng(223),4)
    # Explicit policies and deterministic gates force position-dependent paths.
    from dataclasses import replace
    members[1]=replace(members[1],policy=replace(members[1].policy,target_fraction=.01))
    members[2]=replace(members[2],policy=replace(members[2].policy,reduce_fraction=1.))
    members[3]=replace(members[3],policy=replace(members[3].policy,swing_left=1,swing_right=1))
    members[4]=replace(members[4],policy=replace(members[4].policy,stop_fraction=.005,trail_fraction=.005))
    members[5]=replace(members[5],minimum_age={"exit":30,"add":10,"reduce":15})
    started=perf_counter();records=[]
    with owned_run(root,version='v7-full-session-qualification-v1'):
        for day in a.days:
            load_start=perf_counter();data=SessionData(a.inputs/day,a.history/day,feature_cache=a.feature_cache/day)
            load_seconds=perf_counter()-load_start
            try:
                # Cache equivalence at tile joins, quote aging and terminal clocks.
                for begin in (0,2040,data.clocks-8):
                    end=min(begin+16,data.clocks);listings=[0,len(data.listing_ids)-1]
                    left=data.feature_block(begin,end,listings);right=data.prepare_feature_block(begin,end,listings)
                    for x,y in zip(left,right):torch.testing.assert_close(x,y,rtol=0,atol=0)
                data.activate('cuda');program_start=perf_counter()
                gates=PopulationPrograms(members,'cuda').evaluate(data)
                torch.cuda.synchronize();program_seconds=perf_counter()-program_start
                data.deactivate()
                subset=SimpleNamespace(device=torch.device("cpu"),feature_cache=data.feature_cache,clocks=data.clocks,listing_ids=data.listing_ids[:4],feature_block=data.feature_block)
                cpu_gates=PopulationPrograms(members,'cpu').evaluate(subset)
                torch.testing.assert_close(cpu_gates,gates[:,:,:4].cpu(),rtol=0,atol=0)
                data.activate('cuda')
                # Exercise all management bits on complete real price/observation
                # and membership grids, without bypassing admission or fill masks.
                clock=torch.arange(data.clocks,device='cuda')[:,None]
                forced=torch.ones((data.clocks,len(data.listing_ids)),dtype=torch.uint8,device='cuda')
                forced|=(clock%7==2).to(torch.uint8)*4
                forced|=(clock%13==5).to(torch.uint8)*8
                forced|=(clock%17==8).to(torch.uint8)*16
                forced|=(clock%101==100).to(torch.uint8)*2
                gates[:6]=forced.to(torch.int16)|(forced.to(torch.int16)<<8)
                host_gates=gates.cpu().numpy();data.deactivate()
                for cost in (0.,10.):
                    execution=Execution(cost_bps=cost);ref_start=perf_counter()
                    expected=replay_reference(data,members,host_gates,execution);ref_seconds=perf_counter()-ref_start
                    data.activate('cuda');replay_start=perf_counter()
                    actual=replay_cohort([data],members,[gates],execution=execution,backend='compile')[0]
                    errors=check(expected,actual);gpu_seconds=perf_counter()-replay_start;data.deactivate()
                    if not bool(actual['terminal_valid'].all()) or float(actual['add_count'].sum())<=0 or float(actual['reduce_count'].sum())<=0:
                        raise ValueError('Full-session lifecycle evidence is missing')
                    records.append(dict(day=day,clocks=data.clocks,listings=len(data.listing_ids),cost_bps=cost,
                        identity=data.identity,feature_receipt_sha256=file_hash(a.feature_cache/day/'complete.json'),
                        load_seconds=load_seconds,program_seconds=program_seconds,reference_seconds=ref_seconds,replay_seconds=gpu_seconds,
                        maximum_absolute_errors=errors,metrics={k:v.cpu().tolist() for k,v in actual.items()}))
                    write_json(root/'progress.json',dict(completed=len(records),records=records,validation_opened=False))
                    print(json.dumps(dict(day=day,cost_bps=cost,status='passed',reference_seconds=ref_seconds,replay_seconds=gpu_seconds)),flush=True)
            finally:data.close();torch.cuda.empty_cache()
        write_json(root/'qualification.json',dict(status='passed',source_sha256=source_hash(),full_session=True,
            full_training_pass=False,validation_opened=False,optimization_started=False,wall_seconds=perf_counter()-started,records=records,
            members=[m.payload() for m in members]))

if __name__=='__main__':main()
