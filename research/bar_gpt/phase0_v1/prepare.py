"""Fixed calibration from the existing frozen training-only subset; no validation fit."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,time
from pathlib import Path
import numpy as np
import torch
from ..phase1_v11.data import verify
from ..phase1_v11.transforms import coordinates,decode,inverse
from ..phase1_v7.full_data import prepare_raw,gpu_inputs
from ..v5.shards.phase1_contract import write_json


def prepare(data,output):
    if not output.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):raise ValueError('Runtime output required')
    if output.exists():raise ValueError('Immutable calibration already exists')
    torch.set_num_threads(4);start=time.monotonic();manifest=verify(data)
    rows=np.load(data/'train.npy',mmap_mode='r');sums=torch.zeros(3,27,dtype=torch.float64);maximum=0.
    for start_row in range(0,len(rows),256):
        b=prepare_raw(rows[start_row:start_row+256],native=True);x,v,_=gpu_inputs(b);c,m=coordinates(x[...,:18],v[...,:18])
        features=torch.cat((c,x[...,18:]),-1).double();mask=torch.cat((m,v[...,18:]),-1)
        sums[0]+=mask.sum((0,1));sums[1]+=torch.where(mask,features,0).sum((0,1));sums[2]+=torch.where(mask,features.square(),0).sum((0,1))
        restored=inverse(decode(c,c.new_zeros((len(c),9)),constrain=False))
        err=((restored-b.raw).abs()/b.raw.abs().clamp_min(1))[v[...,:18]]
        maximum=max(maximum,float(err.max()))
    if not (sums[0]>0).all() or maximum>2e-5:raise ValueError('Calibration/roundtrip failed')
    mean=sums[1]/sums[0];scale=(sums[2]/sums[0]-mean.square()).clamp_min(0).sqrt()
    floor=torch.tensor([1e-5]*18+[.001]*9);scale=torch.maximum(scale,floor)
    result=dict(version='phase0-v1-fixed-coordinates',data_hash=manifest['hash'],split_hash=manifest['source_split_hash'],
        files=manifest['files'],rows=len(rows),bars=len(rows)*64,parameters=dict(mean=mean.tolist(),scale=scale.tolist()),
        counts=sums[0].tolist(),roundtrip_max_relative_over_max_one=maximum,seconds=time.monotonic()-start,
        policy='Uniform bars of frozen training subset only; no sampling weights; fixed statistics; no local or target references; no clipping.')
    output.parent.mkdir(parents=True,exist_ok=True);write_json(output,result);print(result['seconds'],flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset'));p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();prepare(a.data,a.output)
