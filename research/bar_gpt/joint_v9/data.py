"""Causal input caches and training-only calibration; audits stay sealed."""
import json
from pathlib import Path
import numpy as np
import torch
from ..phase1_v8.data import cache as window_cache,statistics
from ..phase1_v8.model import coordinates
from ..phase1_v6.model import Transform
from ..v5.shards.phase1_contract import digest,file_hash


def load(root,partitions=('train','development','novel_development','tiny')):
    root=Path(root);manifest=json.loads((root/'manifest.json').read_text())
    if digest({k:v for k,v in manifest.items() if k!='hash'})!=manifest['hash']:raise ValueError('Dataset manifest changed')
    rows={}
    for name in partitions:
        path=root/(name+'.npy')
        if file_hash(path)!=manifest['files'][path.name]:raise ValueError('Dataset payload changed')
        rows[name]=np.load(path,mmap_mode='r',allow_pickle=False)
    return rows,manifest


def anchor(x,valid,check=True):
    """Historical persistence in structured coordinates; no target argument."""
    latest=x[:,7,-1,:18].clone();v=valid[:,7,-1,:18]
    paired=v[:,6:10].all(-1)&v[:,12:16].all(-1)
    for base in (6,12):latest[:,base:base+4]=torch.where(paired[:,None],latest[:,base:base+4],latest[:,3:4])
    for j in (4,5,10,11,16,17):latest[:,j]=torch.where(v[:,j],latest[:,j],0)
    from ..phase1_v7.model import geometry,quote_geometry
    mask=torch.ones_like(latest[:,None],dtype=torch.bool)
    code=geometry(latest[:,None],mask,check=check)
    code[...,12:16]=quote_geometry(latest[:,None],mask,check=check)[0]
    return code[:,0]


def cache(rows):
    c=window_cache(rows['context'].reshape(-1))
    x=c['values'].reshape(len(rows),16,64,27);v=c['valid'].reshape_as(x)
    target=Transform.logs(torch.from_numpy(rows['future']['values'].copy()),torch.from_numpy(rows['future']['valid'].astype(bool))).float()
    starts=torch.from_numpy(rows['context']['end_us'][...,0].copy());ends=torch.from_numpy(rows['context']['end_us'][...,-1].copy())
    origin=torch.from_numpy(rows['origin_us'].copy())[:,None]
    ages=torch.stack((torch.log1p((origin-starts).double()/1e6),torch.log1p((origin-ends).double()/1e6)),-1).float()
    if (ages<0).any():raise ValueError('Future input age')
    return dict(x=x,valid=v,ages=ages,target=target,target_valid=torch.from_numpy(rows['future']['valid'].astype(bool)))


def calibrate(train):
    x=train['x'].reshape(-1,64,27);v=train['valid'].reshape_as(x)
    mean,scale,_=statistics(x,v,.001)
    code,mask=coordinates(x[...,:18],v[...,:18],'joint');cm,cs,_=statistics(code,mask,.0001)
    future,fv=coordinates(train['target'],train['target_valid'],'joint')
    past=anchor(train['x'],train['valid'])[:,None]
    delta=future-past
    # RMS of training target changes sets head units, not target-dependent inversion.
    ds=(torch.where(fv,delta.double().square(),0).sum((0,1))/fv.sum((0,1)).clamp_min(1)).sqrt().clamp_min(.001).float()
    from .model import structured
    residual=train['target']-structured(past)
    ls=(torch.where(train['target_valid'],residual.double().square(),0).sum((0,1))/train['target_valid'].sum((0,1)).clamp_min(1)).sqrt().clamp_min(.001).float()
    bias=torch.where(fv,delta,0).sum(0)/fv.sum(0).clamp_min(1)
    return dict(input_mean=mean,input_scale=scale,reconstruction_mean=cm,reconstruction_scale=cs,forecast_scale=ds,loss_scale=ls,mean_future_delta=bias)
