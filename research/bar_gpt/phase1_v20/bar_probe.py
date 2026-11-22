"""Training-fitted linear probes distinguish bar information loss from basis drift."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json,time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import torch
import wandb
from .encoders import restore
from ..phase0_v3.experiment import cache
from ..phase1_v11.transforms import coordinates,decode
from ..phase0_v1.metrics import Metrics,scalars
from ..phase1_v17.metrics import ActivityMetrics,scalars as activity_scalars
from ..phase1_v12.data import load_teacher
from ..phase1_v11.data import verify
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import file_hash,digest,write_json
from research.mlops.env import discover_env_files,load_env_files


def solve(gram,rhs,ridge=1e-6):
    diagonal=torch.eye(gram.shape[-1],device=gram.device,dtype=gram.dtype);diagonal[-1,-1]=0
    penalty=gram.diagonal(dim1=-2,dim2=-1).mean(-1)
    return torch.linalg.solve(gram+ridge*penalty[:,None,None]*diagonal,rhs[...,None]).squeeze(-1)


@torch.no_grad()
def fit(encode,teacher,data,latent_mean,latent_scale):
    gram=torch.zeros(18,17,17,device='cuda',dtype=torch.float64);rhs=torch.zeros(18,17,device='cuda',dtype=torch.float64);counts=torch.zeros(18,device='cuda',dtype=torch.float64)
    for i in range(0,len(data[0]),512):
        values,valid,_,_=[q[i:i+512] for q in data];z=encode(values,valid)
        x=((z-latent_mean)/latent_scale).double().reshape(-1,16);x=torch.cat((x,torch.ones_like(x[:,:1])),1)
        target,_=coordinates(values[...,:18],valid[...,:18]);target=((target-teacher.mean[:18])/teacher.scale[:18]).double().reshape(-1,18)
        masks=[]
        for b in (0,6,12):
            oc=valid[...,b]&valid[...,b+3]
            masks.extend((valid[...,b+3],oc,oc&valid[...,b+1],oc&valid[...,b+2],valid[...,b+4],valid[...,b+5]))
        mask=torch.stack(masks,-1).reshape(-1,18);weights=mask.double()
        gram+=(weights.T@(x[:,:,None]*x[:,None,:]).flatten(1)).reshape(18,17,17)
        rhs+=torch.where(mask,target,0).T@x;counts+=weights.sum(0)
    if (counts==0).any():raise ValueError('Missing probe target group')
    return solve(gram,rhs),counts


@torch.no_grad()
def evaluate(encode,teacher,data,mean,scale,coefficients):
    metric=Metrics();activity=ActivityMetrics()
    for i in range(0,len(data[0]),512):
        x,v,raw,frames=[q[i:i+512] for q in data];z=(encode(x,v)-mean)/scale
        normalized=(z.double()@coefficients[:,:16].T+coefficients[:,-1]).float()
        code=normalized*teacher.scale[:18]+teacher.mean[:18]
        constrained=torch.stack([code[...,j].clamp_min(0) if j%6 in (2,3,4,5) else code[...,j] for j in range(18)],-1)
        logs=decode(constrained,code.new_zeros(len(code),9),constrain=False);out=dict(normalized=normalized,code=code,logs=logs)
        metric.add(teacher,out,x,v,SimpleNamespace(grid=raw,frames=frames));activity.add(logs,raw,v,frames)
    detailed=metric.result();detailed['activity']=activity.result();measured=scalars(detailed);measured.update({'activity/'+k:v for k,v in activity_scalars(detailed['activity']).items()})
    if measured['nonfinite']:raise FloatingPointError('Nonfinite probe')
    return detailed,measured


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True);p.add_argument('--checkpoints',type=Path,nargs='+',required=True);a=p.parse_args()
    if a.output.exists():raise ValueError('New diagnostic identity required')
    a.output.mkdir();torch.set_num_threads(4);torch.set_float32_matmul_precision('highest');root=Path('D:/TradingML/runtimes/bar_gpt')
    data=root/'phase1-v8-learned-mae-20261117/dataset';verified=verify(data);extra=root/'phase1-v19-training-pool-20261121';sm=json.loads((extra/'manifest.json').read_text())
    if digest({k:v for k,v in sm.items() if k!='hash'})!=sm['hash'] or file_hash(extra/'train.npy')!=sm['files']['train.npy'] or sm['split_hash']!=verified['source_split_hash']:raise ValueError('Training provenance')
    cal=json.loads((root/'phase1-v12-frozen16-mae-20261121/teacher/calibration.json').read_text())
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    with lease(a.output):
        teacher,identity=load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',verified['source_split_hash'],'cuda')
        train=tuple(torch.cat((x,y)) for x,y in zip(cache(np.load(data/'train.npy',mmap_mode='r')),cache(np.load(extra/'train.npy',mmap_mode='r'))));dev=cache(np.load(data/'development.npy',mmap_mode='r'))
        mean=torch.tensor(cal['mean'],device='cuda');scale=torch.tensor(cal['scale'],device='cuda');results=[]
        for checkpoint in [None]+a.checkpoints:
            if checkpoint:
                receipt=json.loads((checkpoint.parent/'result.json').read_text());sha=file_hash(checkpoint)
                if receipt['checkpoint_sha']!=sha:raise ValueError('Candidate changed')
                model=restore(torch.load(checkpoint,map_location='cpu',weights_only=False)).cuda().eval();encode=model.bar.encode;name=checkpoint.parent.name
            else:encode=teacher.encode;sha=identity['checkpoint_sha256'];name='original_phase0'
            started=time.perf_counter();coefficients,counts=fit(encode,teacher,train,mean,scale);detailed,metrics=evaluate(encode,teacher,dev,mean,scale,coefficients)
            result=dict(name=name,checkpoint_sha=sha,source=a.revision,seconds=time.perf_counter()-started,counts=counts.cpu().tolist(),metrics=metrics,data_hash=verified['hash'],supplement_hash=sm['hash'],ridge=1e-6,scope='Diagnostic readout fit on training only; unchanged encoder weights; not a second decoder in the deployed joint model')
            w=wandb.init(entity='mehdifaraji',project='bargpt-phase01-v20-joint',name=a.output.name+'-'+name,dir=str(a.output),config={k:v for k,v in result.items() if k!='metrics'},reinit=True);w.log({'probe/'+k:v for k,v in metrics.items()},step=0);result['wandb_url']=w.url;w.finish()
            write_json(a.output/(name+'-details.json'),detailed);torch.save(coefficients.cpu(),a.output/(name+'-coefficients.pt'));results.append(result);write_json(a.output/'result.json',results);print(name,metrics['raw_mae/1s/trade_close'],metrics['activity/daily/bid_count/raw_mae'],flush=True)
            if checkpoint:del model


if __name__=='__main__':main()
