"""Disposable exact optimizer-state continuations; never writes model checkpoints."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,hashlib,json,time
from pathlib import Path
import numpy as np
import torch
import wandb
from .experiment import optimizer
from .model import initialize
from .temporal_basis import restore_basis
from .objectives import loss
from .joint_base import loss as base_loss
from ..phase0_v1.model import objective as raw_loss
from ..phase0_v3.experiment import cache
from ..phase1_v10.step import stable_clip
from ..phase1_v11.data import verify
from ..phase1_v11.transforms import inverse
from ..phase1_v12.model import positions
from ..phase1_v12.data import load_teacher
from ..phase1_v8.runtime import preflight
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import FRAMES,CHANNELS,file_hash,digest,write_json
from ..phase1_v5_raw.engine import FRAME_NAMES
from research.mlops.env import discover_env_files,load_env_files


def measure(model,inputs,scale):
    captured=[];hook=model.register_forward_hook(lambda m,a,out:captured.append(out))
    try:total,parts=loss(model,*inputs,scale)
    finally:hook.remove()
    latent,_,out=captured[0]
    _,base=base_loss(model,*inputs,scale,latent,out,include_activity=False)
    _,raw=raw_loss(model,out,inputs[0],inputs[1])
    components=dict(raw_price=.5*raw[0],raw_activity=.5*raw[1],coordinate=.1*raw[2],
        body=.5*base['body'],wick=.05*base['wick'],anchor=base['bar_latent_anchor'],
        auxiliary_activity=parts['activity']+.1*parts['activity_direction'],
        range=.1*parts['range']+.05*parts['range_bins'],candle=.5*parts['candle_direction'])
    torch.testing.assert_close(sum(components.values()),total)
    with torch.no_grad():
        _,valid,target,frames,pos,_=inputs;pred=inverse(out['logs']).double();target=target.double()
        if not torch.isfinite(pred).all():raise FloatingPointError('Nonfinite reconstruction')
        known=torch.zeros_like(valid[...,0]).scatter(1,pos,True)
        f=(frames[:,None]==torch.tensor(FRAMES,device=frames.device)).long().argmax(1)
        group=torch.nn.functional.one_hot(f,6).T.double();scores={}
        for name,mask in (('visible',known),('hidden',~known)):
            v=valid[...,:18]&mask[...,None];counts=group@v.double().sum(1)
            errors=group@torch.where(v,(pred-target).abs(),0).sum(1)
            c=counts.cpu().tolist();e=errors.cpu().tolist()
            for fi,frame in enumerate(FRAME_NAMES):
                for j,channel in enumerate(CHANNELS):
                    if c[fi][j]:scores[f'{name}/{frame}/{channel}']=dict(count=c[fi][j],mae=e[fi][j]/c[fi][j])
    return total,dict(loss=float(total.detach()),components={k:float(v.detach()) for k,v in components.items()},raw=scores)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-revision',required=True);p.add_argument('--checkpoint',type=Path,action='append',required=True)
    p.add_argument('--steps',type=int,default=8);a=p.parse_args();a.device='cuda';a.resume=False
    if not 1<=a.steps<=16 or not 1<=len(a.checkpoint)<=2:raise ValueError('Bounded diagnostic')
    preflight(a);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    root=Path('D:/TradingML/runtimes/bar_gpt');base=root/'phase1-v8-learned-mae-20261117/dataset'
    verified=verify(base);extra=root/'phase1-v19-training-pool-20261121';sm=json.loads((extra/'manifest.json').read_text())
    if digest({k:v for k,v in sm.items() if k!='hash'})!=sm['hash'] or file_hash(extra/'train.npy')!=sm['files']['train.npy'] or sm['split_hash']!=verified['source_split_hash']:raise ValueError('Supplement provenance')
    started=time.monotonic();results={};hashes={};sampling=[]
    with lease(a.output):
        teacher,identity=load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',verified['source_split_hash'],'cuda')
        train=tuple(torch.cat((x,y)) for x,y in zip(cache(np.load(base/'train.npy',mmap_mode='r')),cache(np.load(extra/'train.npy',mmap_mode='r'))))
        with torch.no_grad():old=torch.cat([teacher.encode(train[0][i:i+512],train[1][i:i+512]) for i in range(0,len(train[0]),512)])
        train=(*train,old);scale=torch.ones(6,6,device='cuda')
        for cp in a.checkpoint:
            sha=file_hash(cp)
            if sha!=json.loads((cp.parent/'result.json').read_text())['checkpoint_sha']:raise ValueError('Checkpoint changed')
            saved=torch.load(cp,map_location='cpu',weights_only=False);c=saved['contract']
            if c['split_hash']!=verified['source_split_hash'] or c['data_hash']!=verified['hash'] or c['supplement_hash']!=sm['hash'] or c['bar_parent']!=identity:raise ValueError('Contract mismatch')
            if c['arm'] not in ('joint_prefloor','joint_basis_prefloor') or c.get('decoder_visible_only'):raise ValueError('Unsupported continuation contract')
            model=(restore_basis(saved) if c['arm']=='joint_basis_prefloor' else initialize(None,saved,None,'joint_prefloor')).cuda().train()
            opt=optimizer(model,c['lr'],c['bar_lr']);opt.load_state_dict(saved['optimizer'])
            g=torch.Generator(device='cuda');g.set_state(saved['sampling_rng'])
            torch.set_rng_state(saved['rng']);torch.cuda.set_rng_state_all(saved['cuda_rng'])
            hashes[str(cp)]=sha;rows=[];sequence=[]
            for offset in range(1,a.steps+1):
                if time.monotonic()-started>600:raise TimeoutError('Audit deadline')
                step=saved['step']+offset;ids=torch.randint(len(train[0]),(c['batch'],),device='cuda',generator=g)
                x,v,raw,f,z=[q[ids] for q in train];pos=positions(c['batch'],'cuda',64 if step%4==0 else 16,g)
                sequence.append(hashlib.sha256(ids.cpu().numpy().tobytes()+pos.cpu().numpy().tobytes()).hexdigest())
                inputs=(x,v,raw,f,pos,z);opt.zero_grad(set_to_none=True)
                total,before=measure(model,inputs,scale);total.backward();norm=stable_clip(model.parameters())
                if not torch.isfinite(norm):raise FloatingPointError('Nonfinite gradient')
                opt.step()
                with torch.no_grad():_,after=measure(model,inputs,scale)
                rows.append(dict(step=step,visible=pos.shape[1],preclip_gradient_norm=float(norm),before=before,after=after))
                print(cp.parent.name,step,before['loss'],after['loss'],flush=True)
            results[str(cp)]=rows;sampling.append(sequence)
            if file_hash(cp)!=sha:raise ValueError('Input checkpoint mutated')
            del model,opt,saved,total
        if len(sampling)==2 and sampling[0]!=sampling[1]:raise ValueError('Continuations used different examples/masks')
        write_json(a.output/'result.json',dict(source=a.source_revision,data_hash=verified['hash'],supplement_hash=sm['hash'],checkpoints=hashes,
            sampling_hashes=sampling,results=results,seconds=time.monotonic()-started,
            scope='Disposable sequential Adam updates with saved moments/RNG and unchanged LR, masks, clipping and objective. Same-batch before/after measurements, not validation or evidence of sustained generalization. Original checkpoints unchanged.'))
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
        w=wandb.init(entity='mehdifaraji',project='bargpt-phase01-v21-repair',name=a.output.name,dir=str(a.output),config=dict(source=a.source_revision,checkpoints=hashes,steps=a.steps,data_hash=verified['hash']))
        for i in range(a.steps):
            metrics={}
            for cp,rows in results.items():
                name=Path(cp).parent.name;r=rows[i]
                for side in ('before','after'):
                    metrics[f'{name}/{side}/loss']=r[side]['loss']
                    for k,v in r[side]['components'].items():metrics[f'{name}/{side}/{k}']=v
            w.log(metrics,step=i)
        write_json(a.output/'wandb.json',dict(id=w.id,url=w.url));w.finish()


if __name__=='__main__':main()
