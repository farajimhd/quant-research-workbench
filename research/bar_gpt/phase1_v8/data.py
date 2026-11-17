"""Train-only calibration and mask-before-encoder batches; no local statistics."""
import json
from pathlib import Path
import numpy as np
import torch
from .model import coordinates
from ..phase1_v7.train import load_data
from ..phase1_v6.model import Transform
from ..v5.transforms_v1 import BarInputTransformV1


def cache(rows,device='cpu'):
    parts=[];masks=[]
    for start in range(0,len(rows),512):
        r=rows[start:start+512];raw=torch.from_numpy(r['values'].copy());valid=torch.from_numpy(r['valid'].astype(bool))
        logs=Transform.logs(raw,valid).float()
        t=BarInputTransformV1()(raw,valid,torch.from_numpy(r['end_us'].copy()),torch.from_numpy(r['timeframe_us'].copy()))
        t.values[...,:18]=logs;parts.append(t.values);masks.append(t.valid)
    return dict(values=torch.cat(parts).to(device),valid=torch.cat(masks).to(device))


def statistics(values,valid,floor):
    count=valid.sum((0,1));assert (count>0).all()
    mean=torch.where(valid,values.double(),0).sum((0,1))/count
    scale=(torch.where(valid,(values.double()-mean).square(),0).sum((0,1))/count).sqrt().clamp_min(floor)
    return mean.float(),scale.float(),count


def calibrate(data,kind):
    mean,scale,count=statistics(data['values'],data['valid'],.001)
    code,mask=coordinates(data['values'][...,:18],data['valid'][...,:18],kind)
    om,os,oc=statistics(code,mask,.0001)
    return dict(input_mean=mean,input_scale=scale,output_mean=om,output_scale=os),dict(input_count=count.tolist(),output_count=oc.tolist(),
        policy='Fixed train-only valid-observation mean/std. Uniform selected observations; training sampling weights do not affect calibration or evaluation. No target/window references.')


def batch(data,ids,visible,generator,full=False):
    target=data['values'][ids];valid=data['valid'][ids];n=len(ids);device=target.device
    if full:positions=torch.arange(64,device=device).expand(n,-1)
    else:positions=torch.rand(n,64,device=device,generator=generator).argsort(1)[:,:visible].sort(1).values
    take=positions[...,None].expand(-1,-1,27)
    selected=torch.ones(n,64,device=device,dtype=torch.bool)
    if not full:selected.scatter_(1,positions,False)
    return target.gather(1,take),valid.gather(1,take),positions,target,valid,selected
