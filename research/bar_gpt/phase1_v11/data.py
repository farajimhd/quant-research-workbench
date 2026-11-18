"""Frozen train-only calibration and complete source-valid roundtrip audit."""
import json,time
from pathlib import Path
import numpy as np
import torch
from ..phase1_v8.data import cache,statistics,batch
from ..v5.shards.phase1_contract import file_hash,write_json
from .transforms import coordinates,references,center,decode,inverse


def verify(root,parts=('train','development','tiny')):
    m=json.loads((root/'manifest.json').read_text())
    for part in parts:
        for name in (part+'.npy',part+'-identities.json'):
            if name in m['files'] and file_hash(root/name)!=m['files'][name]:raise ValueError('Data hash: '+name)
    return m


def prepare(root,output):
    started=time.monotonic();m=verify(root);rows=np.load(root/'train.npy',allow_pickle=False);data=cache(rows)
    mean,scale,count=statistics(data['values'],data['valid'],.001)
    generator=torch.Generator().manual_seed(110731);refs=[];masks=[];maximum=0.;raw_relative=0.;observations=0
    for start in range(0,len(rows),256):
        ids=torch.arange(start,min(start+256,len(rows)))
        x,v,p,target,valid,h=batch(data,ids,16,generator)
        ref,rv=references(x[...,:18],v[...,:18],mean[[3,4,5,9,10,11,15,16,17]])
        refs.append(ref);masks.append(rv)
        code,cm=coordinates(target[...,:18],valid[...,:18]);restored=decode(center(code,ref),ref)
        keep=valid[...,:18];delta=(restored-target[...,:18]).abs()[keep]
        maximum=max(maximum,float(delta.max()));observations+=int(keep.sum())
        original=torch.from_numpy(rows[start:start+len(ids)]['values'].copy()).double()
        relative=((inverse(restored)-original).abs()/original.abs().clamp_min(1))[keep]
        raw_relative=max(raw_relative,float(relative.max()))
    if maximum>2e-5:raise AssertionError(f'Source-valid roundtrip fails: {maximum}')
    ref=torch.cat(refs);rv=torch.cat(masks)
    rm,rs,rc=statistics(ref[:,None],rv[:,None],.01)
    result=dict(version=11,data_hash=m['hash'],files=m['files'],rows=len(rows),seed=110731,
        parameters=dict(time_mean=mean[18:].tolist(),time_scale=scale[18:].tolist(),reference_mean=rm.tolist(),reference_scale=rs.tolist()),
        reference_count=rc.tolist(),time_count=count[18:].tolist(),roundtrip=dict(observations=observations,max_log_absolute=maximum,max_raw_error_over_max_one_target=raw_relative),
        constant_logs=mean[:18].tolist(),seconds=time.monotonic()-started,
        policy='Uniform selected training observations only. References use deterministic random16 visible bars; no sampling weights. Metrics uniform selected examples, balanced across timeframe/channel. Original source-valid masks unchanged.')
    output.parent.mkdir(parents=True,exist_ok=True);write_json(output,result);return result
