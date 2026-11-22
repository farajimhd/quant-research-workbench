"""Bounded training-data audit of activity decoder optimizer coordinates.

Fresh one-step Adam diagnostics are not a resumed-optimizer or training result.
"""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse
import copy
import json
import time
from pathlib import Path
import numpy as np
import torch
from torch.nn import functional as F
from .temporal_basis import ActivityBasisLinear
from ..phase1_v20.encoders import restore
from ..phase0_v3.experiment import cache
from ..phase1_v11.data import verify
from ..phase1_v12.model import positions
from ..phase1_v8.runtime import preflight
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import file_hash,write_json


def change_loss(code,raw,valid,known,constrain):
    total=code.new_zeros((),dtype=torch.float64)
    for j in (4,5,10,11,16,17):
        mask=valid[...,j]
        target=torch.where(mask,raw[...,j].double(),0).log1p()
        pred=code[...,j].double()
        if constrain:pred=pred.clamp_min(0)
        pair=mask[:,1:]&mask[:,:-1]&known[:,1:]&known[:,:-1]
        delta=pred.diff(dim=1);actual=target.diff(dim=1)
        moving=pair&(actual!=0)
        mse=torch.where(pair,(delta-actual).square(),0).sum()/pair.sum().clamp_min(1)
        ce=F.binary_cross_entropy_with_logits(delta/.1,(actual>0).double(),reduction='none')
        total+=(.5*mse+.1*torch.where(moving,ce,0).sum()/moving.sum().clamp_min(1))/6
    return total


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-revision',required=True);p.add_argument('--checkpoint',type=Path,required=True)
    a=p.parse_args();a.device='cuda';a.resume=False;preflight(a)
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest');torch.manual_seed(917)
    data=Path('D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset')
    verified=verify(data,parts=('train',));sha=file_hash(a.checkpoint)
    if sha!=json.loads((a.checkpoint.parent/'result.json').read_text())['checkpoint_sha']:
        raise ValueError('Parent hash mismatch')
    saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False)
    if saved['contract']['split_hash']!=verified['source_split_hash']:raise ValueError('Split mismatch')
    started=time.monotonic();results=[]
    with lease(a.output):
        model=restore(saved).cuda().eval().requires_grad_(False)
        x,v,raw,frames=cache(np.load(data/'train.npy',mmap_mode='r')[:4096])
        for visible in (16,64):
            pos=positions(len(x),'cuda',visible)
            with torch.no_grad():
                embeddings=[]
                for i in range(0,len(x),512):
                    embeddings.append(model(x[i:i+512],v[i:i+512],pos[i:i+512])[1])
                z=torch.cat(embeddings).detach()
            known=torch.zeros_like(v[...,0]).scatter(1,pos,True)
            for constrain in (False,True):
                for kind in ('native','basis'):
                    head=copy.deepcopy(model.decoder) if kind=='native' else ActivityBasisLinear(model.decoder)
                    head.requires_grad_(True)
                    with torch.no_grad():
                        reference=model.decoder(z).reshape(-1,64,18)*model.scale+model.mean
                        initial=head(z).reshape(-1,64,18)*model.scale+model.mean
                        torch.testing.assert_close(initial,reference,atol=3e-5,rtol=3e-5)
                        prices=torch.tensor([j for j in range(18) if j%6<4],device='cuda')
                        torch.testing.assert_close(initial[:,:,prices],reference[:,:,prices],atol=0,rtol=0)
                    opt=torch.optim.AdamW(head.parameters(),lr=1e-6,weight_decay=0.)
                    before=change_loss(head(z).reshape(-1,64,18)*model.scale+model.mean,raw,v,known,constrain)
                    before.backward()
                    if not all(torch.isfinite(p.grad).all() for p in head.parameters()):raise FloatingPointError('Gradient')
                    torch.nn.utils.clip_grad_norm_(head.parameters(),1.)
                    opt.step()
                    with torch.no_grad():
                        after=head(z).reshape(-1,64,18)*model.scale+model.mean
                        delta=(after-initial)[:,:,[4,5,10,11,16,17]].double()
                        row=dict(visible=visible,constrain=constrain,kind=kind,loss_before=float(before),
                            loss_after=float(change_loss(after,raw,v,known,constrain)),
                            initialization_max_abs=float((initial-reference).abs().max()),
                            activity_log_level_drift_mean=float(delta.mean(1).abs().mean()),
                            activity_log_level_drift_max=float(delta.mean(1).abs().max()),
                            activity_log_change_update_mean=float(delta.diff(dim=1).abs().mean()),
                            negative_activity_codes=int((initial[:,:,[4,5,10,11,16,17]]<0).sum()))
                        results.append(row);print(json.dumps(row),flush=True)
            if time.monotonic()-started>600:raise TimeoutError('Audit deadline')
        write_json(a.output/'result.json',dict(source=a.source_revision,parent_sha=sha,data_hash=verified['hash'],
            split_hash=verified['source_split_hash'],training_prefix_windows=len(x),results=results,
            seconds=time.monotonic()-started,scope='Fresh single Adam step, activity change objective only, frozen real training embeddings. No checkpoint mutation, no validation, no full-objective or resumed-optimizer inference.'))


if __name__=='__main__':main()
