"""Frozen range-loss output gradients and training-pool bin prevalence."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json,time
from pathlib import Path
import numpy as np
import torch
import wandb
from research.mlops.env import discover_env_files,load_env_files
from .gap_audit import cache,verify,digest,file_hash,write_json,preflight,lease,restore_basis,positions,FRAMES,FRAME_NAMES
from .objectives import range_edges


def range_gradient(prediction,target):
    """d(.1*Huber(bp error)+.05*ordinal BCE)/d(predicted log range)."""
    edges=range_edges(prediction.device)
    shape=1000*((prediction-target)*10000).clamp(-1,1)
    ordinal=500*(torch.sigmoid((prediction[...,None]-edges)*10000)-(target[...,None]>edges).double()).mean(-1)
    return shape,ordinal


@torch.no_grad()
def evaluate(model,data):
    result={};x,v,raw,frames=data;began=time.monotonic()
    for visible in (16,64):
        sums=torch.zeros(6*3*6,7,device='cuda',dtype=torch.float64)
        g=torch.Generator(device='cuda').manual_seed(917)
        for i in range(0,len(x),512):
            if time.monotonic()-began>600:raise TimeoutError('Frozen audit deadline')
            xx,vv,rr,ff=[q[i:i+512] for q in data]
            pos=positions(len(xx),'cuda',visible,g);_,_,out=model(xx,vv,pos)
            known=torch.zeros_like(vv[...,0]).scatter(1,pos,True)
            weight=torch.where(known,1.,.1).double()
            fi=(ff[:,None]==torch.tensor(FRAMES,device='cuda')).long().argmax(1)
            for family,b in enumerate((0,6,12)):
                valid=vv[...,b+1]&vv[...,b+2]
                target=torch.log(torch.where(valid,rr[...,b+1].double(),1)/torch.where(valid,rr[...,b+2].double(),1))
                pred=out['logs'][...,b+1].double()-out['logs'][...,b+2].double()
                if (valid&(target<0)).any():raise ValueError('Negative source range requires explicit accounting')
                bucket=torch.where(target==0,0,1+torch.bucketize(target.contiguous(),range_edges(target.device),right=False))
                shape,ordinal=range_gradient(pred,target);gradient=shape+ordinal
                index=(fi[:,None]*18+family*6+bucket).flatten()
                fields=torch.stack((torch.ones_like(target),weight,weight*shape,weight*ordinal,
                    weight*gradient.clamp_min(0),weight*(-gradient).clamp_min(0),(pred-target)*10000),-1)
                sums.scatter_add_(0,index[:,None].expand(-1,7),torch.where(valid[...,None],fields,0).reshape(-1,7))
        rows=[]
        for k,row in enumerate(sums.cpu().tolist()):
            n,w,shape,ordinal,narrow,widen,signed=row
            if not n:continue
            f,rest=divmod(k,18);family,b=divmod(rest,6)
            rows.append(dict(timeframe=FRAME_NAMES[f],family=('trade','bid','ask')[family],bin=b,count=n,
                weight_sum=w,shape_gradient_sum=shape,ordinal_gradient_sum=ordinal,narrowing_gradient_sum=narrow,
                widening_gradient_sum=widen,signed_error_bp=signed/n))
        result[str(visible)]=rows
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--source-revision',required=True);a=p.parse_args();a.device='cuda';a.resume=False
    preflight(a);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    root=Path('D:/TradingML/runtimes/bar_gpt');base=root/'phase1-v8-learned-mae-20261117/dataset'
    verified=verify(base);extra=root/'phase1-v19-training-pool-20261121';sm=json.loads((extra/'manifest.json').read_text())
    if digest({k:v for k,v in sm.items() if k!='hash'})!=sm['hash'] or file_hash(extra/'train.npy')!=sm['files']['train.npy'] or sm['split_hash']!=verified['source_split_hash']:raise ValueError('Supplement provenance')
    sha=file_hash(a.checkpoint)
    if sha!=json.loads((a.checkpoint.parent/'result.json').read_text())['checkpoint_sha']:raise ValueError('Checkpoint changed')
    saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False)
    if saved['contract']['split_hash']!=verified['source_split_hash']:raise ValueError('Split mismatch')
    with lease(a.output):
        began=time.monotonic();model=restore_basis(saved).cuda().eval()
        train=tuple(torch.cat((x,y)) for x,y in zip(cache(np.load(base/'train.npy',mmap_mode='r')),cache(np.load(extra/'train.npy',mmap_mode='r'))))
        dev=cache(np.load(base/'development.npy',mmap_mode='r'))
        results={s:evaluate(model,d) for s,d in (('train',train),('development',dev))}
        if file_hash(a.checkpoint)!=sha:raise ValueError('Checkpoint mutated')
        write_json(a.output/'result.json',dict(source=a.source_revision,checkpoint_sha=sha,data_hash=verified['hash'],supplement_hash=sm['hash'],
            seconds=time.monotonic()-began,results=results,bins=['zero','0-0.05pct','0.05-0.1pct','0.1-0.25pct','0.25-0.5pct','above0.5pct'],
            scope='Frozen experiment pool, not full dataset. Analytical range-only derivative at output log range before family/batch averaging, with actual visible1/hidden0.1 weights. Positive means local narrowing. Excludes other losses, model Jacobian, Adam and clipping; not parameter-update attribution.'))
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
        run=wandb.init(entity='mehdifaraji',project='bargpt-phase01-v21-repair',name=a.output.name,dir=str(a.output),config=dict(source=a.source_revision,checkpoint_sha=sha,data_hash=verified['hash']))
        metrics={}
        for split,modes in results.items():
            for mode,rows in modes.items():
                for row in rows:
                    prefix=f'{split}/visible{mode}/{row["timeframe"]}/{row["family"]}/bin{row["bin"]}'
                    for key in ('count','signed_error_bp','narrowing_gradient_sum','widening_gradient_sum'):metrics[prefix+'/'+key]=row[key]
        run.log(metrics,step=0);write_json(a.output/'wandb.json',dict(id=run.id,url=run.url));run.finish()


if __name__=='__main__':main()
