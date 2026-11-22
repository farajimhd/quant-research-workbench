"""Bounded CPU precision/variance/price-band diagnostics on completed candidates."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
import numpy as np
import torch
from .model import restore
from ..phase1_v7.full_data import prepare_raw,gpu_inputs
from ..phase1_v12.model import positions
from ..phase1_v11.transforms import inverse,PRICES,coordinates
from ..v5.shards.phase1_contract import write_json,file_hash


def main():
    p=argparse.ArgumentParser();p.add_argument('--runtime-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--windows',type=int,default=512);a=p.parse_args()
    torch.set_num_threads(2);root=a.output;rows=json.loads((root/'comparison.json').read_text())
    data=np.load(a.runtime_root/'phase1-v8-learned-mae-20261117/dataset/development.npy',mmap_mode='r')
    if not 1<=a.windows<=len(data):raise ValueError('Requested diagnostic window count')
    ids=np.linspace(0,len(data)-1,a.windows,dtype=np.int64);chosen=data[ids]
    for row in rows:
        out=root/row['name']/('diagnostics-v3.json' if a.windows==512 else f'diagnostics-{a.windows}.json')
        if out.exists():continue
        started=time.time();cp=out.parent/'checkpoint_last.pt'
        if file_hash(cp)!=row['checkpoint_sha']:raise ValueError('Checkpoint identity')
        m=restore(torch.load(cp,map_location='cpu',weights_only=False)).eval();pred=[];target=[];masks=[];embeddings=[];codes=[];target_codes=[]
        with torch.no_grad():
            for start in range(0,len(chosen),64):
                b=prepare_raw(chosen[start:start+64],True);x,v,_=gpu_inputs(b);_,e,y=m(x,v,positions(len(x),'cpu',64))
                pred.append(inverse(y['logs']));target.append(b.grid.double());masks.append(v[...,:18]);embeddings.append(e.flatten(1))
                codes.append(y['code']);target_codes.append(coordinates(x[...,:18],v[...,:18])[0])
        pred=torch.cat(pred);target=torch.cat(target);valid=torch.cat(masks);e=torch.cat(embeddings).double()
        e=e[torch.linspace(0,len(e)-1,min(512,len(e))).long()];e=e-e.mean(0)
        spectrum=torch.linalg.svdvals(e).square();rank=float(spectrum.sum().square()/spectrum.square().sum().clamp_min(1e-30))
        standardized=e/e.std(0,unbiased=False).clamp_min(1e-12)
        normalized_spectrum=torch.linalg.svdvals(standardized).square()
        normalized_rank=float(normalized_spectrum.sum().square()/normalized_spectrum.square().sum().clamp_min(1e-30))
        pm=[];tm=[]
        for j in range(18):
            pm.append(float(pred[...,j][valid[...,j]].var(unbiased=False)));tm.append(float(target[...,j][valid[...,j]].var(unbiased=False)))
        q=target[...,list(PRICES)];r=pred[...,list(PRICES)];mask=valid[...,list(PRICES)];bands=[]
        for low,high in zip((0,.1,1,5,20,100),(.1,1,5,20,100,float('inf'))):
            keep=mask&(q>=low)&(q<high)&(q>0);errors=(r-q).abs()[keep];rel=100*errors/q[keep]
            bands.append(dict(low=low,high=None if high==float('inf') else high,count=int(keep.sum()),mae=float(errors.mean()) if len(errors) else None,
                mean_pct=float(rel.mean()) if len(rel) else None,median_pct=float(rel.median()) if len(rel) else None,p90_pct=float(rel.quantile(.9)) if len(rel) else None))
        violations={}
        for base,name in ((0,'trade'),(6,'bid'),(12,'ask')):
            keep=valid[...,base:base+4].all(-1);o,h,l,c=pred[...,base:base+4].unbind(-1)
            violations[name]=int((keep&((h<torch.maximum(o,c))|(l>torch.minimum(o,c)))).sum())
        quote=valid[...,6:10]&valid[...,12:16]
        codes=torch.cat(codes);target_codes=torch.cat(target_codes);shape_stats=[]
        for base in (0,6,12):
            keep=valid[...,base:base+4].all(-1)
            for j in range(base+1,base+4):
                q=codes[...,j][keep];y=target_codes[...,j][keep]
                shape_stats.append(dict(coordinate=j,count=int(keep.sum()),prediction_mean=float(q.mean()),target_mean=float(y.mean()),prediction_std=float(q.std()),target_std=float(y.std()),prediction_negative_pct=float((q<0).float().mean()*100)))
        result=dict(scope=f'{a.windows} evenly spaced development windows; CPU FP32, all-visible; rank at most512windows; separate from GPU metrics',
            checkpoint_sha=row['checkpoint_sha'],embedding_values=e.shape[1],embedding_participation_rank=rank,standardized_embedding_participation_rank=normalized_rank,
            rank_caution='Variance participation rank is diagnostic, not proof of absent information; it depends on coordinate scales and sampled examples.',
            prediction_variance=pm,target_variance=tm,shape_coordinate_stats=shape_stats,price_bands=bands,nonpositive_price_denominators=int((mask&(target[...,list(PRICES)]<=0)).sum()),
            tiny_price_denominators_lt_1e_8=int((mask&(target[...,list(PRICES)]>0)&(target[...,list(PRICES)]<1e-8)).sum()),nonfinite=int((~torch.isfinite(pred)&valid).sum()),
            negative_predictions=int(((pred<0)&valid).sum()),ohlc_violations=violations,quote_violations=int(((pred[...,6:10]>pred[...,12:16])&quote).sum()),seconds=time.time()-started)
        write_json(out,result);print(row['name'],rank,'seconds',result['seconds'],flush=True)


if __name__=='__main__':main()
