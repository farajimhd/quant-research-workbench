"""CPU-only visible-context controls and transform audit; no hidden-value oracle."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import torch
from ..phase0_v1.metrics import Metrics,scalars
from ..phase1_v17.metrics import ActivityMetrics,scalars as activity_scalars
from ..phase1_v7.full_data import prepare_raw,gpu_inputs
from ..phase1_v12.model import positions,gather
from ..phase1_v11.transforms import coordinates,decode,inverse
from ..v5.shards.phase1_contract import file_hash,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--runtime-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    torch.set_num_threads(2);start=time.time();root=a.runtime_root
    cp=root/'phase1-v21-body-20261121/body_direction/checkpoint.pt'
    saved=torch.load(cp,map_location='cpu',weights_only=False);s=saved['model']
    model=SimpleNamespace(mean=s['mean'],scale=s['scale'],loss_factor=s['loss_factor'])
    data=root/'phase1-v8-learned-mae-20261117/dataset';manifest=json.loads((data/'manifest.json').read_text())
    if file_hash(data/'development.npy')!=manifest['files']['development.npy']:raise ValueError('Frozen development changed')
    rows=np.load(data/'development.npy',mmap_mode='r');g=torch.Generator().manual_seed(917)
    names=('constant_masked','constant_all_visible','nearest_visible_masked')
    metrics={k:Metrics('cpu') for k in names};activities={k:ActivityMetrics('cpu') for k in names}
    audit=dict(valid_observations=0,max_log_roundtrip_error=0.,max_raw_error_over_max_one_target=0.,quote_projection_changed_valid_fields=0)
    with torch.no_grad():
        for i in range(0,len(rows),512):
            b=prepare_raw(rows[i:i+512],True);x,v,_=gpu_inputs(b);n=len(x);pos=positions(n,'cpu',16,g)
            # CPU RNG differs from CUDA RNG: baseline masks are fixed/reproducible,
            # but must not be described as exactly matched candidate mask draws.
            known=torch.zeros_like(v[...,0]).scatter(1,pos,True);hidden=v&~known[...,None]
            code=model.mean.expand(n,64,-1);constant=decode(code,torch.zeros(n,9),constrain=False)
            vx=gather(x[...,:18],pos);vv=gather(v[...,:18],pos)
            distance=(torch.arange(64)[None,:,None]-pos[:,None,:]).abs()
            index=distance[...,None].expand(-1,-1,-1,18).masked_fill(~vv[:,None],10000).argmin(2)
            nearest=vx.gather(1,index);nearest=torch.where(vv.any(1)[:,None],nearest,constant)
            outputs=dict(constant_masked=(constant,hidden),constant_all_visible=(constant,v),nearest_visible_masked=(nearest,hidden))
            for name,(logs,mask) in outputs.items():
                c,_=coordinates(logs,mask[...,:18]);out=dict(logs=logs,code=c,normalized=(c-model.mean)/model.scale)
                metrics[name].add(model,out,x,mask,b);activities[name].add(logs,b.grid,mask,b.frames)
            c,_=coordinates(x[...,:18],v[...,:18]);restored=decode(c,torch.zeros(n,9),constrain=False,pair_quotes=False);m=v[...,:18]
            audit['valid_observations']+=int(m.sum());audit['max_log_roundtrip_error']=max(audit['max_log_roundtrip_error'],float((restored-x[...,:18]).abs()[m].max()))
            audit['max_raw_error_over_max_one_target']=max(audit['max_raw_error_over_max_one_target'],float(((inverse(restored)-b.grid).abs()/b.grid.abs().clamp_min(1))[m].max()))
            paired=decode(c,torch.zeros(n,9),constrain=False)
            audit['quote_projection_changed_valid_fields']+=int(((paired-restored).abs()>1e-6).logical_and(m).sum())
    detailed={k:dict(reconstruction=metrics[k].result(),activity=activities[k].result()) for k in names}
    measured={k:dict(scalars(detailed[k]['reconstruction']),**{'activity/'+name:value for name,value in activity_scalars(detailed[k]['activity']).items()}) for k in names}
    write_json(a.output/'baseline-detail.json',detailed);write_json(a.output/'baseline-metrics.json',measured)
    write_json(a.output/'development-transform-audit.json',dict(audit,seconds=time.time()-start,data_sha=manifest['files']['development.npy'],
        note='Baseline CPU masks seed917; same distribution, not identical CUDA draws. Nearest visible per-channel; missing channel falls back to training coordinate mean.'))
    print(json.dumps(dict(audit,seconds=time.time()-start,close_1s={k:v['raw_mae/1s/trade_close'] for k,v in measured.items()})))


if __name__=='__main__':main()
