"""Training-only frozen window probes; never a decoder replacement or forecast."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
import argparse
import json
import time
from pathlib import Path
import numpy as np
import torch
import wandb
from ..phase1_v20.encoders import restore, Bar, BarConfig, Window, Config, JointAutoencoder
from ..phase0_v3.experiment import cache
from ..phase1_v12.model import positions
from ..phase1_v11.data import verify
from ..phase1_v16.probe import summary
from ..phase1_v8.runtime import preflight
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import file_hash, digest, write_json
from research.mlops.env import discover_env_files, load_env_files


def fit(x, y, ridge=.01):
    x=x.double(); y=y.double()
    center=x.mean(0); scale=x.std(0, unbiased=False).clamp_min(1e-8)
    a=(x-center)/scale; target_center=y.mean(0)
    coefficient=torch.linalg.solve(a.T@a/len(a)+ridge*torch.eye(a.shape[1], device=a.device, dtype=a.dtype),
                                   a.T@(y-target_center)/len(a))
    return center,scale,target_center,coefficient


def predict(x, solution):
    center,scale,target_center,coefficient=solution
    return (x.double()-center)/scale@coefficient+target_center


def targets(data, channel, kind):
    _,valid,raw,_=data
    if kind=='candle':
        mask=(valid[...,channel]&valid[...,channel+3]).all(1)
        x=raw[mask].double()
        target=(x[...,channel+3]/x[...,channel]).log()*10000
    else:
        mask=valid[...,channel].all(1); x=raw[mask,:,channel].double()
        target=x.log() if kind=='price' else x.log1p()
        if kind=='change': target=target.diff(dim=1)*10000
    return mask,target


def scores(pred, target, kind):
    if not torch.isfinite(pred).all(): raise FloatingPointError('Nonfinite probe prediction')
    if kind in ('candle','change'): return summary(pred,target)
    raw_pred=pred.exp() if kind=='price' else pred.expm1()
    raw_target=target.exp() if kind=='price' else target.expm1()
    if not torch.isfinite(raw_pred).all(): raise FloatingPointError('Nonfinite inverse probe')
    return dict(count=target.numel(), log_mae=float((pred-target).abs().mean()),
                raw_mae=float((raw_pred-raw_target).abs().mean()), negative=int((raw_pred<0).sum()),
                r2=float(1-(pred-target).square().sum()/(target-target.mean(0)).square().sum().clamp_min(1e-20)))


def raw_summary(data):
    x,v,_,_=data; x=x[...,:18].double(); v=v[...,:18]
    count=v.sum(1).clamp_min(1); mean=torch.where(v,x,0).sum(1)/count
    std=(torch.where(v,(x-mean[:,None]).square(),0).sum(1)/count).sqrt()
    return torch.cat((mean,std,torch.where(v[:,0],x[:,0],0),torch.where(v[:,-1],x[:,-1],0),v.sum(1)/64),-1)


def fresh(saved):
    s=saved['model']; c=saved['contract']
    bar=Bar(dict(mean=s['bar.mean'],scale=s['bar.scale']),BarConfig(**c['bar_config']),c['input_calibration'])
    temporal=Window(Config(**c['temporal_config']),s['temporal.mean'],s['temporal.scale'],'mask_adapter').encoder
    return JointAutoencoder(bar,temporal,s['mean'],s['scale'],torch.zeros_like(s['decoder.weight']),torch.zeros_like(s['decoder.bias']))


@torch.no_grad()
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-revision',required=True);p.add_argument('--checkpoint',action='append',type=Path,required=True)
    a=p.parse_args();a.device='cuda';a.resume=False
    if len(a.checkpoint)>3:raise ValueError('Bounded checkpoint count')
    preflight(a);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    root=Path('D:/TradingML/runtimes/bar_gpt');base=root/'phase1-v8-learned-mae-20261117/dataset'
    verified=verify(base);extra=root/'phase1-v19-training-pool-20261121'
    sm=json.loads((extra/'manifest.json').read_text())
    if digest({k:v for k,v in sm.items() if k!='hash'})!=sm['hash'] or file_hash(extra/'train.npy')!=sm['files']['train.npy'] or sm['split_hash']!=verified['source_split_hash']:
        raise ValueError('Training supplement provenance')
    results={};started=time.monotonic()
    with lease(a.output):
        train=tuple(torch.cat((x,y)) for x,y in zip(cache(np.load(base/'train.npy',mmap_mode='r')),cache(np.load(extra/'train.npy',mmap_mode='r'))))
        dev=cache(np.load(base/'development.npy',mmap_mode='r'));data=(train,dev)
        tasks=[(b+3,'price') for b in (0,6,12)]+[(b,'candle') for b in (0,6,12)]
        tasks += [(j,kind) for j in (4,5,10,11,16,17) for kind in ('activity','change')]
        target_cache={f'{j}_{kind}':[targets(d,j,kind) for d in data] for j,kind in tasks}
        sources={}
        for label in [str(cp) for cp in a.checkpoint]+['untrained','raw_summary']:
            if time.monotonic()-started>1200:raise TimeoutError('Probe deadline')
            if label=='raw_summary':features=[raw_summary(d) for d in data]
            else:
                cp=a.checkpoint[0] if label=='untrained' else Path(label)
                sha=file_hash(cp);proof=cp.with_suffix('.proof.json')
                expected=json.loads(proof.read_text())['sha256'] if proof.exists() else json.loads((cp.parent/'result.json').read_text())['checkpoint_sha']
                if sha!=expected:raise ValueError('Checkpoint changed')
                saved=torch.load(cp,map_location='cpu',weights_only=False)
                if saved['contract']['split_hash']!=verified['source_split_hash']:raise ValueError('Split mismatch')
                sources[label]=sha;torch.manual_seed(917)
                model=(fresh(saved) if label=='untrained' else restore(saved)).cuda().eval()
                features=[]
                for x,v,raw,frames in data:
                    features.append(torch.cat([model(x[i:i+512],v[i:i+512],positions(len(x[i:i+512]),'cuda',64))[1]
                                               for i in range(0,len(x),512)]))
                del model,saved
            rows={}
            for j,kind in tasks:
                key=f'{j}_{kind}';(tm,y),(dm,dy)=target_cache[key]
                counts=dict(train_total=len(tm),train_included=int(tm.sum()),development_total=len(dm),development_included=int(dm.sum()))
                if min(int(tm.sum()),int(dm.sum()))<2:raise ValueError('Insufficient valid probe labels')
                solution=fit(features[0][tm],y);pred=predict(features[1][dm],solution)
                groups={}
                for frame in dev[3][dm].unique():
                    take=dev[3][dm]==frame
                    groups[str(int(frame))]=scores(pred[take],dy[take],kind)
                rows[key]=dict(counts=counts,train=scores(predict(features[0][tm],solution),y,kind),development=scores(pred,dy,kind),by_frame=groups,
                               constant_development=scores(y.mean(0).expand_as(dy),dy,kind))
            results[label]=rows
            write_json(a.output/'result.json',dict(source=a.source_revision,data_hash=verified['hash'],supplement_hash=sm['hash'],
                split_hash=verified['source_split_hash'],checkpoints=sources,ridge=.01,results=results,seconds=time.monotonic()-started,
                scope='All-visible frozen512D window probes; each channel fit globally across timeframes on training only. Complete source-valid channel windows only; exclusions counted. Same-window labels, no forecasts. Raw summary is input-derived diagnostic control; readouts are not deployed decoders.'))
            del features
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
        run=wandb.init(entity='mehdifaraji',project='bargpt-phase01-v21-full20',name=a.output.name,dir=str(a.output),
            config=dict(source=a.source_revision,data_hash=verified['hash'],supplement_hash=sm['hash'],checkpoints=sources,ridge=.01,scope='Frozen window probes, not forecasting'))
        payload={}
        def flatten(prefix,value):
            for k,v in value.items():
                if isinstance(v,dict):flatten(prefix+'/'+k,v)
                elif isinstance(v,(int,float)):payload[prefix+'/'+k]=v
        for name,rows in results.items():flatten(Path(name).parent.name if name not in ('untrained','raw_summary') else name,rows)
        run.log(payload,step=0);write_json(a.output/'wandb.json',dict(url=run.url));run.finish()


if __name__=='__main__':main()
