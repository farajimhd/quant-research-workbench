"""Matched raw-unit train/development diagnostic; no fitting or checkpoint edits."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json,time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import torch
import wandb
from .raw_metrics import Metrics,scalars
from .temporal_basis import restore_basis
from ..phase1_v20.encoders import restore
from ..phase1_v17.metrics import ActivityMetrics,scalars as activity_scalars
from ..phase0_v3.experiment import cache
from ..phase1_v11.data import verify
from ..phase1_v11.transforms import inverse
from ..phase1_v12.model import positions
from ..phase1_v8.runtime import preflight
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import FRAMES,file_hash,digest,write_json
from ..phase1_v5_raw.engine import FRAME_NAMES
from research.mlops.env import discover_env_files,load_env_files

BANDS=('below_0.10','0.10_to_1','1_to_5','5_to_20','20_to_100','100_plus')


def price_bands(value):
    return torch.bucketize(value,torch.tensor([.1,1.,5.,20.,100.],device=value.device,dtype=value.dtype),right=True)


class CloseBands:
    def __init__(self):
        self.parts=[];self.tiny=0;self.zero=0;self.negative=0

    def add(self,pred,raw,valid,frames):
        y=raw[:,:,[3,9,15]].double();p=pred[:,:,[3,9,15]].double();v=valid[:,:,[3,9,15]]
        self.zero+=int((v&(y==0)).sum());self.negative+=int((v&(y<0)).sum())
        self.tiny+=int((v&(y>0)&(y<=1e-8)).sum())
        keep=v&(y>1e-8)
        f=(frames[:,None]==torch.tensor(FRAMES,device=y.device)).long().argmax(1)
        group=f[:,None,None]*18+torch.arange(3,device=y.device)[None,None,:]*6+price_bands(y)
        error=(p-y).abs();relative=100*error/torch.where(keep,y,1)
        self.parts.append((group[keep],error[keep],relative[keep]))

    def result(self):
        group,error,relative=[torch.cat([p[j] for p in self.parts]) for j in range(3)]
        rows={}
        for k in group.unique().tolist():
            take=group==k;e=error[take];r=relative[take]
            frame,rest=divmod(k,18);family,band=divmod(rest,6)
            key=f'{FRAME_NAMES[frame]}/{("trade","bid","ask")[family]}/{BANDS[band]}'
            q=torch.quantile(r,torch.tensor([.5,.9],device=r.device,dtype=r.dtype))
            rows[key]=dict(count=len(r),raw_mae=float(e.mean()),mean_pct=float(r.mean()),median_pct=float(q[0]),p90_pct=float(q[1]))
        return dict(rows=rows,zero_denominators=self.zero,tiny_positive_denominators=self.tiny,
            negative_targets=self.negative,relative_exclusion='Source-valid targets<=1e-8 excluded from relative metrics only; remain in main raw metrics. Close channels only.')


@torch.no_grad()
def evaluate(model,data):
    x,v,raw,frames=data;results={};g=torch.Generator(device='cuda').manual_seed(917)
    masks=[positions(min(512,len(x)-i),'cuda',16,g) for i in range(0,len(x),512)]
    for mode in ('masked','all_visible'):
        metric=Metrics();activity=ActivityMetrics();bands=CloseBands();sums=squares=None;count=0
        began=time.monotonic()
        for bi,i in enumerate(range(0,len(x),512)):
            xx,vv=x[i:i+512],v[i:i+512];r,f=raw[i:i+512],frames[i:i+512]
            pos=masks[bi] if mode=='masked' else positions(len(xx),'cuda',64)
            _,z,out=model(xx,vv,pos)
            vm=vv&(~torch.zeros_like(vv[...,0]).scatter(1,pos,True))[...,None] if mode=='masked' else vv
            b=SimpleNamespace(grid=r,frames=f)
            metric.add(model,out,xx,vm,b);activity.add(out['logs'],r,vm,f)
            bands.add(inverse(out['logs']),r,vm,f)
            zz=z.double();zs=zz.sum(0);zq=zz.square().sum(0)
            sums=zs if sums is None else sums+zs;squares=zq if squares is None else squares+zq;count+=len(z)
        detailed=metric.result();ad=activity.result();variance=(squares/count-(sums/count).square()).clamp_min(0)
        if detailed['nonfinite']:raise FloatingPointError('Nonfinite reconstruction')
        results[mode]=dict(metrics=scalars(detailed),activity_metrics=activity_scalars(ad),raw_detail=detailed,
            activity_detail=ad,close_bands=bands.result(),embedding_variance=dict(mean=float(variance.mean()),
            minimum=float(variance.min()),dimensions_below_1e_minus_10=int((variance<1e-10).sum()),windows=count),seconds=time.monotonic()-began)
    return results


@torch.no_grad()
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-revision',required=True);p.add_argument('--checkpoint',type=Path,action='append',required=True)
    a=p.parse_args();a.device='cuda';a.resume=False
    if not 1<=len(a.checkpoint)<=3:raise ValueError('At most three frozen models')
    preflight(a);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    root=Path('D:/TradingML/runtimes/bar_gpt');base=root/'phase1-v8-learned-mae-20261117/dataset'
    verified=verify(base);extra=root/'phase1-v19-training-pool-20261121';sm=json.loads((extra/'manifest.json').read_text())
    if digest({k:v for k,v in sm.items() if k!='hash'})!=sm['hash'] or file_hash(extra/'train.npy')!=sm['files']['train.npy'] or sm['split_hash']!=verified['source_split_hash']:
        raise ValueError('Supplement provenance')
    started=time.monotonic();results={};hashes={}
    with lease(a.output):
        train=tuple(torch.cat((x,y)) for x,y in zip(cache(np.load(base/'train.npy',mmap_mode='r')),cache(np.load(extra/'train.npy',mmap_mode='r'))))
        dev=cache(np.load(base/'development.npy',mmap_mode='r'))
        for cp in a.checkpoint:
            sha=file_hash(cp)
            if sha!=json.loads((cp.parent/'result.json').read_text())['checkpoint_sha']:raise ValueError('Checkpoint changed')
            saved=torch.load(cp,map_location='cpu',weights_only=False)
            if saved['contract']['split_hash']!=verified['source_split_hash']:raise ValueError('Checkpoint split mismatch')
            model=(restore_basis(saved) if saved['contract'].get('decoder_parameterization') in ('activity_dct64_v1','activity_dct64_nonlinear32_v1') else restore(saved)).cuda().eval()
            label=str(cp);hashes[label]=sha;results[label]={}
            for split,data in (('train',train),('development',dev)):
                if time.monotonic()-started>1200:raise TimeoutError('Diagnostic deadline')
                results[label][split]=evaluate(model,data)
                print(cp.parent.name,split,'complete',flush=True)
            del model,saved
            write_json(a.output/'result.json',dict(source=a.source_revision,data_hash=verified['hash'],supplement_hash=sm['hash'],
                split_hash=verified['source_split_hash'],checkpoints=hashes,results=results,seconds=time.monotonic()-started,
                scope='Frozen train/development reconstruction, identical masks across models, raw source-valid cells. Distribution differences mean a train/dev gap alone is not proof of overfit. No fitting, no future labels.'))
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
        w=wandb.init(entity='mehdifaraji',project='bargpt-phase01-v21-repair',name=a.output.name,dir=str(a.output),config=dict(source=a.source_revision,checkpoints=hashes,data_hash=verified['hash'],supplement_hash=sm['hash']))
        payload={}
        for cp,splits in results.items():
            for split,modes in splits.items():
                for mode,row in modes.items():
                    prefix=f'{Path(cp).parent.name}/{split}/{mode}'
                    for k,v in row['metrics'].items():
                        if k.startswith('raw_mae/') or k.endswith('moving_direction_pct'):payload[prefix+'/'+k]=v
                    for k,v in row['activity_metrics'].items():
                        if k.endswith('moving_direction_pct'):payload[prefix+'/activity/'+k]=v
                    for k,r in row['close_bands']['rows'].items():
                        for name in ('count','mean_pct','median_pct','p90_pct'):payload[prefix+'/close_band/'+k+'/'+name]=r[name]
        w.log(payload,step=0);write_json(a.output/'wandb.json',dict(id=w.id,url=w.url));w.finish()


if __name__=='__main__':main()
