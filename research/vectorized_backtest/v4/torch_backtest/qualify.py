"""Workstation synthetic CPU/CUDA financial and rule parity qualification.

Real full-session B128 profile evidence is also mandatory; synthetic parity
alone cannot publish qualification for a historical campaign.
"""
import argparse,json,time
from pathlib import Path
import torch
import numpy as np
from .runtime import code_hash,file_hash,require_runtime,write_json,configure_caches
from .fixtures import synthetic_tape
from .genome import StrategySpace
from .evolution import sample,STAGES
from .program_runner import ProgramRunner
from .program import TorchPrograms
from .feature_bank import CATALOG

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--profile',type=Path,required=True);p.add_argument('--sessions',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args(argv)
    output=require_runtime(args.output);configure_caches(output);torch.set_num_threads(1)
    profile=json.loads(args.profile.read_text())
    if not torch.cuda.is_available():raise RuntimeError('CUDA workstation qualification required')
    if profile.get('code_hash')!=code_hash() or profile.get('population')!=128 or profile.get('sessions_sha256')!=file_hash(args.sessions):raise ValueError('Full-session profile identity mismatch')
    from .evolution import Individual
    from .program import Node,Op
    space=StrategySpace();population=sample(np.random.default_rng(12),space,4)
    for individual in population:
        individual.policy=space.default.tolist();individual.policy[4]=1;individual.policy[11]=0;individual.policy[12]=0
    source=synthetic_tape(seconds=90,listings=3)
    gates={s:torch.ones((90,4,3),dtype=torch.bool) if s in ('entry','trail','replacement') else torch.zeros((90,4,3),dtype=torch.bool) for s in STAGES};gates['exit'][30:]=True
    cpu=ProgramRunner(source,space,population,gates,backend='eager',maximum_fills=512).compile();left=cpu.run()
    gpu=ProgramRunner(source.to('cuda'),space,population,{s:v.cuda() for s,v in gates.items()},backend='compiled_graph',maximum_fills=512,graph_steps=32).compile();right=gpu.run()
    for name in ('cash','equity','realized','fees','drawdown','fill_count','sold_share_seconds','capital_dollar_seconds','stop_risk_dollar_seconds','open_positions'):
        torch.testing.assert_close(left[name],right[name].cpu(),rtol=1e-10,atol=1e-8)
    for lane in range(4):
        count=int(cpu.fill_count[lane]);assert count>0
        torch.testing.assert_close(cpu.ledger[lane,:count],gpu.ledger[lane,:count].cpu(),rtol=1e-10,atol=1e-8)
    x=torch.rand(250,len(CATALOG));valid=torch.rand(250,len(CATALOG))>.1
    for stage in STAGES:
        programs=[v.programs()[stage] for v in population]
        a,b=TorchPrograms(programs,CATALOG)(x,valid);c,d=TorchPrograms(programs,CATALOG,'cuda')(x.cuda(),valid.cuda())
        torch.testing.assert_close(a,c.cpu(),rtol=1e-5,atol=1e-6);assert torch.equal(b,d.cpu())
    report=dict(status='passed',code_hash=code_hash(),population=128,sessions_sha256=file_hash(args.sessions),profile_sha256=file_hash(args.profile),full_session_profile=profile['receipts'],synthetic_full_ledger_parity=True,rule_cpu_cuda_parity=True,qualified_epoch=time.time())
    write_json(output/'qualification.json',report);print(str(output/'qualification.json'));return 0
if __name__=='__main__':raise SystemExit(main())
