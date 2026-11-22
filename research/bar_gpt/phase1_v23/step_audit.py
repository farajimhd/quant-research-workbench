"""CPU-only diagnostic: functional changes after one matched Adam update."""
import argparse,json
from pathlib import Path
import numpy as np
import torch
from .model import build,optimizer
from ..phase1_v7.full_data import prepare_raw,gpu_inputs
from ..phase1_v12.data import load_teacher
from ..phase1_v12.model import positions
from ..phase1_v21.objectives import loss
from ..phase1_v10.step import stable_clip


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    output=p.parse_args().output;root=output.parent;torch.set_num_threads(2)
    manifest=json.loads((output/'manifest.json').read_text())
    saved=torch.load(root/'phase1-v22-capacity-20261122/e07-slots32-wide16-s559/checkpoint_last.pt',map_location='cpu',weights_only=False)
    template={k:saved[k] for k in ('model','contract','parent_template','architecture')};del saved
    rows=np.load(root/'phase1-v8-learned-mae-20261117/dataset/train.npy',mmap_mode='r')
    ids=np.linspace(0,len(rows)-1,64,dtype=int);batch=prepare_raw(rows[ids],True);x,v,_=gpu_inputs(batch)
    teacher,_=load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',manifest['split_hash'],'cpu')
    with torch.no_grad():z=teacher.encode(x,v)
    results=[]
    for visible in (16,64):
        torch.manual_seed(559);pos=positions(len(x),'cpu',visible)
        for width,depth,scaled in ((128,2,False),(256,4,False),(384,6,False),(256,4,True),(384,6,True)):
            torch.manual_seed(17);model=build(template,width,depth);opt=optimizer(model,width_scaled=scaled)
            with torch.no_grad():before=model(x,v,pos)[2]['logs'].clone()
            value,_=loss(model,x,v,batch.grid,batch.frames,pos,z,torch.ones(6,6));value.backward();stable_clip(model.parameters());opt.step()
            with torch.no_grad():after=model(x,v,pos)[2]['logs'];post,_=loss(model,x,v,batch.grid,batch.frames,pos,z,torch.ones(6,6))
            mask=v[...,[3,9,15]];change=(after-before)[...,[3,9,15]][mask].abs()
            results.append(dict(width=width,depth=depth,width_scaled_lr=scaled,visible=visible,loss_before=float(value.detach()),loss_after=float(post),mean_absolute_logclose_change=float(change.mean()),p90_absolute_logclose_change=float(change.quantile(.9))))
            del model,opt,value,post
    report=dict(description='CPU FP32, one update on 64 fixed training windows; diagnostic only, not a validation or LR selection experiment',row_indices=ids.tolist(),results=results)
    (output/'one-step-audit.json').write_text(json.dumps(report,indent=2));print(json.dumps(results,indent=2))


if __name__=='__main__':main()
