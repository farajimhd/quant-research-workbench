"""Bounded CPU/CUDA correctness check; never an optimization or full-pass profile."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import torch
from research.vectorized_backtest.v6.torch_backtest.program import Node,Program,Op
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,configure_caches,write_json
from .data import SessionData
from .genome import Individual,Policy,STAGES
from .evaluator import replay_cohort,Execution,PopulationPrograms


def member():
    return Individual({s:Program((Node(Op.CONSTANT,value=int(s=='entry'),unit='bool'),),0) for s in STAGES},
        Policy(cooldown=1,target_fraction=.9,add_minimum_profit=0.,reduce_minimum_profit=0.)).validate()


def synthetic(device):
    mark=torch.tensor([10.,10.,11.,12.,13.,12.],dtype=torch.float64,device=device)[:,None]
    return SimpleNamespace(device=torch.device(device),clocks=6,listing_ids=[0],tensors=dict(mark=mark,
        observed=torch.ones_like(mark,dtype=torch.bool),membership=torch.ones_like(mark,dtype=torch.bool),
        history_ids=torch.arange(6,device=device)[:,None].to(torch.int32)),
        swing_bank=lambda members:(torch.full((6,1),float('nan'),dtype=torch.float64,device=device),torch.zeros(len(members),dtype=torch.int64,device=device)))


def compare(expected,actual):
    for left,right in zip(expected,actual):
        for name in left:torch.testing.assert_close(left[name].cpu(),right[name].cpu(),rtol=1e-10,atol=1e-9,equal_nan=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--inputs',type=Path);p.add_argument('--history',type=Path)
    a=p.parse_args();root=require_runtime(a.output);configure_caches(root/'cache')
    if bool(a.inputs)!=bool(a.history):p.error('Both actual input and history paths are required together')
    members=[member(),member(),member()];checks=[]
    gates_cpu=[torch.tensor([1,4,8,0,0,0],dtype=torch.uint8)[None,:,None].expand(3,-1,-1).clone() for _ in range(2)]
    cpu=replay_cohort([synthetic('cpu'),synthetic('cpu')],members,gates_cpu,execution=Execution(cost_bps=10))
    for backend in ('eager','compile'):
        gpu=replay_cohort([synthetic('cuda'),synthetic('cuda')],members,[v.cuda() for v in gates_cpu],execution=Execution(cost_bps=10),backend=backend)
        compare(cpu,gpu);checks.append('synthetic CPU vs CUDA '+backend)
    if a.inputs:
        data=SessionData(a.inputs,a.history,device='cpu')
        data.clocks=min(128,data.clocks);data.listing_ids=data.listing_ids[:4]
        data.host_tensors={k:v[:data.clocks,:len(data.listing_ids)].clone() for k,v in data.host_tensors.items()}
        data.deactivate();shared=PopulationPrograms(members,'cpu');gates=shared.evaluate(data,chunk=64)
        actual_cpu=replay_cohort([data],members,[gates])
        data.activate('cuda');shared_gpu=PopulationPrograms(members,'cuda');gpu_gates=shared_gpu.evaluate(data,chunk=64)
        torch.testing.assert_close(gates,gpu_gates.cpu(),rtol=0,atol=0)
        actual_gpu=replay_cohort([data],members,[gpu_gates],backend='compile')
        compare(actual_cpu,actual_gpu);checks.append('certified training prefix feature gates and CPU/CUDA position metrics')
        identity=data.identity;data.close()
    else:identity=None
    write_json(root/'qualification.json',dict(status='passed',checks=checks,actual_input_identity=identity,
        full_session=False,full_training_pass=False,optimization_started=False,validation_opened=False))
    print(json.dumps(dict(status='passed',checks=checks)))

if __name__=='__main__':main()
