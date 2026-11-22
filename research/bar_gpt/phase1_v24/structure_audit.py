"""Post-training native-GPU structure and variance audit, with ownership admission."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,gc,json,time
from pathlib import Path
import numpy as np
import torch
from .model import restore
from ..phase0_v3.experiment import cache
from ..phase1_v11.transforms import inverse
from ..phase1_v11.data import verify
from ..phase1_v12.model import positions
from .runtime import preflight
from ..v5.shards.phase1_contract import file_hash,write_json


@torch.no_grad()
def audit(model,data,count):
    x,valid,raw,_=data;torch.manual_seed(671)
    sums=torch.zeros(5,18,device=x.device,dtype=torch.float64)
    zsum=torch.zeros(64,32,device=x.device,dtype=torch.float64);zsquare=zsum.clone()
    totals=dict(nonfinite=0,negative=0,ohlc_violations=0,ohlc_valid_bars=0,quote_crossings=0,quote_valid_fields=0)
    for i in range(0,len(x),512):
        xx,v,r=x[i:i+512],valid[i:i+512],raw[i:i+512].double();pos=positions(len(xx),x.device,count)
        _,z,out=model(xx,v,pos);p=inverse(out['logs']).double();mask=v[...,:18].clone()
        if count==16:
            known=torch.zeros_like(mask[...,0]).scatter(1,pos,True);mask&=~known[...,None]
        totals['nonfinite']+=int((~p.isfinite()&mask).sum());totals['negative']+=int(((p<0)&mask).sum())
        for base in (0,6,12):
            keep=mask[...,base:base+4].all(-1);o,h,l,c=p[...,base:base+4].unbind(-1)
            totals['ohlc_valid_bars']+=int(keep.sum())
            totals['ohlc_violations']+=int((keep&((h<torch.maximum(o,c))|(l>torch.minimum(o,c))|(h<l))).sum())
        keep=mask[...,6:10]&mask[...,12:16]
        totals['quote_valid_fields']+=int(keep.sum());totals['quote_crossings']+=int((keep&(p[...,6:10]>p[...,12:16])).sum())
        a=torch.where(mask,p,0);b=torch.where(mask,r,0)
        sums+=torch.stack((mask.sum((0,1)),a.sum((0,1)),a.square().sum((0,1)),b.sum((0,1)),b.square().sum((0,1))))
        z=z.double();zsum+=z.sum(0);zsquare+=z.square().sum(0)
    n=sums[0].clamp_min(1);pv=(sums[2]/n-(sums[1]/n).square()).clamp_min(0);tv=(sums[4]/n-(sums[3]/n).square()).clamp_min(0)
    zv=(zsquare/len(x)-(zsum/len(x)).square()).clamp_min(0)
    totals.update(valid_count=sums[0].tolist(),prediction_mean=(sums[1]/n).tolist(),target_mean=(sums[3]/n).tolist(),prediction_variance=pv.tolist(),target_variance=tv.tolist(),
        embedding_mean_std=float(zv.sqrt().mean()),embedding_min_std=float(zv.sqrt().min()),embedding_constant_coordinates=int((zv<1e-16).sum()),embedding_coordinates=zv.numel())
    return totals


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True)
    p.add_argument('--campaign-root',type=Path,action='append',required=True);a=p.parse_args()
    a.device='cuda';a.source_revision=a.revision;a.resume=False;preflight(a)
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest');start=time.time()
    dataset=Path('D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset');identity=verify(dataset)
    data=cache(np.load(dataset/'development.npy',mmap_mode='r'));records=[]
    for folder in a.campaign_root:
        manifest=json.loads((folder/'manifest.json').read_text())
        if manifest['data_hash']!=identity['hash']:raise ValueError('Data identity changed')
        for result_path in sorted(folder.glob('e*/result.json')):
            result=json.loads(result_path.read_text());cp=result_path.parent/'checkpoint_last.pt'
            if file_hash(cp)!=result['checkpoint_sha']:raise ValueError('Checkpoint identity changed')
            saved=torch.load(cp,map_location='cpu',weights_only=False);model=restore(saved).cuda().eval();del saved
            row=dict(name=result['name'],checkpoint_sha=result['checkpoint_sha'],all_visible=audit(model,data,64),masked=audit(model,data,16))
            records.append(row);write_json(a.output/'results.json',dict(source=a.revision,data_hash=identity['hash'],windows=len(data[0]),mask_seed=671,post_training_diagnostic=True,seconds=time.time()-start,results=records))
            print(row['name'],{k:row['all_visible'][k] for k in ('nonfinite','ohlc_violations','quote_crossings','embedding_constant_coordinates')},flush=True)
            del model;gc.collect();torch.cuda.empty_cache()


if __name__=='__main__':main()
