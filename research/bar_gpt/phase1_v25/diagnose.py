"""CPU-only post-training family gradient diagnostic on fixed training rows."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
import numpy as np
import torch
from .model import restore
from .objectives import loss
from ..phase1_v7.full_data import prepare_raw,gpu_inputs
from ..phase1_v12.model import positions
from ..v5.shards.phase1_contract import write_json,file_hash


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--dataset',type=Path,required=True);a=p.parse_args();torch.set_num_threads(4)
    rows=np.load(a.dataset/'train.npy',mmap_mode='r');ids=np.linspace(0,len(rows)-1,64).astype(int);b=prepare_raw(rows[ids],True);x,v,_=gpu_inputs(b);records=[]
    for name in ('e02-shared-light-s559','e03-shared-none-s559'):
        arm=a.output/name;cp=arm/'checkpoint_last.pt';receipt=json.loads((arm/'result.json').read_text())
        if file_hash(cp)!=receipt['checkpoint_sha']:raise ValueError('Changed checkpoint')
        saved=torch.load(cp,map_location='cpu',weights_only=False);m=restore(saved);params=list(m.branches.parameters());del saved
        pos=positions(len(x),'cpu',64);out,_=m(x,v,pos);_,parts=loss(m,out,x,v,b.grid,pos)
        grads=[torch.autograd.grad(parts[k],params,retain_graph=i==0) for i,k in enumerate(('price','activity'))]
        norms=[torch.stack([g.double().square().sum() for g in gs]).sum().sqrt() for gs in grads]
        dot=sum((ga.double()*gb.double()).sum() for ga,gb in zip(*grads));cos=float(dot/(norms[0]*norms[1]).clamp_min(1e-30))
        records.append(dict(name=name,checkpoint_sha=receipt['checkpoint_sha'],price_loss=float(parts['price'].detach()),activity_loss=float(parts['activity'].detach()),price_gradient_norm=float(norms[0]),activity_gradient_norm=float(norms[1]),price_to_activity_gradient_ratio=float(norms[0]/norms[1]),gradient_cosine=cos))
        del m,grads,out,parts
    write_json(a.output/'gradient-diagnosis.json',dict(device='cpu',precision='fp32',training_row_indices=ids.tolist(),visibility=64,post_training_only=True,limitations='64 training windows; diagnostic local gradients, not a causal attribution or population estimate',results=records));print(json.dumps(records,indent=2))
if __name__=='__main__':main()
