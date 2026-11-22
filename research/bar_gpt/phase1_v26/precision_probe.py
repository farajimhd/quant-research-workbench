"""Bounded TF32 qualification; retain highest precision if quality changes."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,gc,json,math,time
from pathlib import Path
import numpy as np
import torch
from .run_full import ROOT,PARENT_SHA
from .model import restore
from .full_step import backward
from ..phase1_v7.full_data import prepare_raw
from ..phase1_v12.model import positions
from ..phase1_v12.data import load_teacher
from ..phase1_v25.data import load
from ..phase1_v25.metrics import evaluate,summary
from ..phase1_v24.runtime import preflight
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import write_json,file_hash


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True);a=p.parse_args()
    a.device='cuda';a.resume=False;preflight(a);torch.cuda.set_per_process_memory_fraction(.85);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    cp=ROOT/'phase1-v26-preserve-20261122/e03-independent-s559/checkpoint_best.pt'
    if file_hash(cp)!=PARENT_SHA:raise ValueError('Parent changed')
    with lease(a.output):
        _,dev,_,proof=load(ROOT);saved=torch.load(cp,map_location='cpu',weights_only=False);model=restore(saved).cuda();model.configure('independent')
        teacher,_=load_teacher(ROOT/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',proof['split_hash'],'cuda')
        rows=np.load(ROOT/'phase1-v8-learned-mae-20261117/dataset/train.npy',mmap_mode='r');batch=prepare_raw(rows[:128],True).to('cuda')
        masks=[(positions(128,'cuda',n,torch.Generator(device='cuda').manual_seed(991)),positions(128,'cuda',56,torch.Generator(device='cuda').manual_seed(992))) for n in (16,64)]
        results={};baseline=[]
        for precision in ('highest','high'):
            torch.set_float32_matmul_precision(precision);errors=[];start=time.perf_counter()
            for i,(pp,ap) in enumerate(masks):
                model.zero_grad(set_to_none=True);value,_,_=backward(model,teacher,batch,pp,ap)
                grads=torch.cat([p.grad.flatten().double() for p in model.parameters() if p.grad is not None])
                if precision=='highest':baseline.append((float(value),grads.clone()))
                else:errors.append(dict(loss_relative=abs(float(value)-baseline[i][0])/abs(baseline[i][0]),gradient_relative=float((grads-baseline[i][1]).norm()/baseline[i][1].norm())))
            model.zero_grad(set_to_none=True);detail,metrics=evaluate(model,dev)
            write_json(a.output/(precision+'-metrics.json'),metrics)
            results[precision]=dict(summary=summary(metrics),gradient_errors=errors,seconds=time.perf_counter()-start)
        base=json.loads((a.output/'highest-metrics.json').read_text());high=json.loads((a.output/'high-metrics.json').read_text());bs=summary(base);hs=summary(high)
        ratios=[high[k]/v for k,v in base.items() if k.startswith('all_visible/raw_mae/') and v>0]
        maximum_direction=max(abs(hs[k]-bs[k]) for k in ('candle','range','activity'))
        accepted=max(ratios)<1.02 and min(ratios)>.98 and maximum_direction<.05 and max(x['gradient_relative'] for x in results['high']['gradient_errors'])<.01
        write_json(a.output/'result.json',dict(accepted=accepted,source=a.source_revision,parent_sha=PARENT_SHA,raw_ratio_min=min(ratios),raw_ratio_max=max(ratios),max_direction_pp=maximum_direction,results=results))
        print(json.dumps(dict(accepted=accepted,raw_ratio_min=min(ratios),raw_ratio_max=max(ratios),max_direction_pp=maximum_direction,results=results)),flush=True)


if __name__=='__main__':main()
