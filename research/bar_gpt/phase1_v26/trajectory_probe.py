"""Matched optimizer trajectories qualify rounding-changing engineering kernels.

This is distinct from strict per-element gradient parity: failures of that gate
remain recorded. No objective, masking, model size, or optimizer change is made.
"""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,copy,gc,json,time
from pathlib import Path
import numpy as np
import torch
from .model import restore
from .train import optimizer
from .step import Replay
from .attention_kernels import install
from ..phase0_v3.experiment import cache
from ..phase1_v12.model import positions
from ..phase1_v12.data import load_teacher
from ..phase1_v25.metrics import evaluate
from ..phase1_v24.runtime import preflight
from ..phase2_v2.train import lease,load_checkpoint
from ..v5.shards.phase1_contract import file_hash,write_json,digest


def compare(base,actual):
    ratios={k:actual[k]/v for k,v in base.items() if '/raw_mae/' in k and v>1e-12}
    differences={k:abs(actual[k]-v) for k,v in base.items() if any(s in k for s in ('direction_pct','accuracy_pct','median_pct','p90_pct'))}
    direction={k:v for k,v in differences.items() if 'direction_pct' in k or 'accuracy_pct' in k}
    relative={k:actual[k]/v for k,v in base.items() if ('median_pct' in k or 'p90_pct' in k) and v>1e-8}
    nonfinite={k:actual[k] for k in actual if 'nonfinite' in k and actual[k]>base.get(k,0)}
    accepted=all(.98<=v<=1.02 for v in ratios.values()) and all(v<=.1 for v in direction.values()) and all(.98<=v<=1.02 for v in relative.values()) and not nonfinite
    return dict(accepted=accepted,raw_ratio_min=min(ratios.values()),raw_ratio_max=max(ratios.values()),
        worst_raw_group=max(ratios,key=ratios.get),max_direction_pp=max(direction.values()),
        worst_direction_group=max(direction,key=direction.get),relative_ratio_min=min(relative.values()),relative_ratio_max=max(relative.values()),nonfinite_regressions=nonfinite)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--modes',default='baseline,tiny_pad');p.add_argument('--steps',type=int,default=128)
    a=p.parse_args();a.device='cuda';a.resume=False;preflight(a)
    if not 1<=a.steps<=256:raise ValueError('Bounded trajectory')
    modes=a.modes.split(',')
    if modes[0]!='baseline':raise ValueError('Baseline first')
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest');torch.cuda.set_per_process_memory_fraction(.85)
    root=a.output.parent
    with lease(a.output):
        peek=torch.load(a.checkpoint,map_location='cpu',weights_only=False);saved=load_checkpoint(a.checkpoint,peek['contract']);del peek
        teacher,_=load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',saved['contract']['split_hash'],'cuda')
        dataset=root/'phase1-v8-learned-mae-20261117/dataset'
        train=cache(np.load(dataset/'train.npy',mmap_mode='r'));dev=cache(np.load(dataset/'development.npy',mmap_mode='r'))
        with torch.no_grad():anchor=teacher.encode(train[0],train[1])
        # Teacher output is shared and exact for this comparison. Separately
        # qualified raw pipeline includes teacher execution and GPU preparation.
        result=dict(source=a.source_revision,checkpoint_sha=file_hash(a.checkpoint),contract=digest(saved['contract']),
            kernel_sha=file_hash(Path(__file__).with_name('attention_kernels.py')),steps=a.steps,batch=1024,
            train_hash=file_hash(dataset/'train.npy'),development_hash=file_hash(dataset/'development.npy'),cases=[])
        reference={}
        for mode in modes:
            model=restore(saved).cuda();model.configure('independent');install(model,teacher,mode)
            opt=optimizer(model,dict(price=3e-7,activity=1e-6));opt.load_state_dict(copy.deepcopy(saved['optimizer']))
            gen=torch.Generator(device='cuda').manual_seed(73391);graphs={};case=dict(mode=mode);started=time.perf_counter()
            for point in ('before','after'):
                _,metrics=evaluate(model,dev);write_json(a.output/f'{mode}-{point}.json',metrics)
                if mode=='baseline':reference[point]=metrics
                else:case[point]=compare(reference[point],metrics)
                if point=='after':break
                model.train()
                for i in range(a.steps):
                    ids=torch.randint(len(train[0]),(1024,),device='cuda',generator=gen)
                    pp=positions(1024,'cuda',64 if (saved['step']+i+1)%4==0 else 16,gen);ap=positions(1024,'cuda',56,gen)
                    batch=(*[t[ids] for t in train],anchor[ids],pp,ap)
                    if pp.shape[1] not in graphs:graphs[pp.shape[1]]=Replay(model,batch)
                    value,_,norm=graphs[pp.shape[1]](batch)
                    if not bool(torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Trajectory')
                    opt.step()
            case['seconds']=time.perf_counter()-started;case['accepted']=mode=='baseline' or case['before']['accepted'] and case['after']['accepted']
            result['cases'].append(case);write_json(a.output/'result.json',result);print(json.dumps(case),flush=True)
            graphs.clear();del model,opt,batch;gc.collect();torch.cuda.empty_cache()


if __name__=='__main__':main()
