"""Training-only linear anchor fitting to diagnose/read out frozen embeddings."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,time
from pathlib import Path
import numpy as np
import torch
from sklearn.linear_model import Ridge
from .data import load,cache
from .model import Config,JointModel,structured
from ..phase1_v8.model import coordinates,inverse,PRICES,ACTIVITY
from ..phase1_v8.runtime import preflight
from ..v5.shards.phase1_contract import write_json,file_hash


@torch.no_grad()
def features(model,data,device,deadline=None):
    model.eval();z=[];targets=[];masks=[]
    for start in range(0,len(data['x']),1024):
        if deadline is not None and time.time()>deadline:raise TimeoutError('Anchor fit deadline')
        x=data['x'][start:start+1024,7].to(device);v=data['valid'][start:start+1024,7].to(device)
        z.append(model.encoder(x,v).cpu().numpy())
        code,valid=coordinates(x[:,-1:,:18],v[:,-1:,:18],'joint')
        targets.append(code[:,0].cpu().numpy());masks.append(valid[:,0].cpu().numpy())
    return np.concatenate(z),np.concatenate(targets),np.concatenate(masks)


def fit(model,training):
    z,target,valid=training;x=z.astype(np.float64);mean=x.mean(0);std=x.std(0);std=np.where(std>1e-8,std,1.)
    y=(target-model.reconstruction_mean.cpu().numpy())/model.reconstruction_scale.cpu().numpy()
    standardized=(x-mean)/std;coefficients=np.zeros((18,x.shape[1]));bias=np.zeros(18);groups={}
    for j in range(18):groups.setdefault(valid[:,j].tobytes(),[]).append(j)
    for ids in groups.values():
        keep=valid[:,ids[0]]
        if keep.sum()<4:raise ValueError('Insufficient valid training anchors')
        regression=Ridge(alpha=.001,solver='cholesky').fit(standardized[keep],y[keep][:,ids])
        weight=regression.coef_/std;coefficients[ids]=weight;bias[ids]=regression.intercept_-weight@mean
    weights=torch.tensor(coefficients,dtype=torch.float32,device=model.reconstruction.weight.device)
    intercept=torch.tensor(bias,dtype=torch.float32,device=weights.device)
    if not torch.isfinite(weights).all() or not torch.isfinite(intercept).all():raise FloatingPointError('Nonfinite fitted anchor')
    with torch.no_grad():model.reconstruction.weight[-54:-36].copy_(weights);model.reconstruction.bias[-54:-36].copy_(intercept)
    return dict(alpha=.001,training_counts=valid.sum(0).tolist(),fit='Training-only standardized Ridge; statistics folded into fixed linear weights. No reference input at inference.')


@torch.no_grad()
def evaluate(model,extracted,raw_target=None,source_valid=None):
    z,target,valid=extracted;device=model.reconstruction.weight.device
    code=torch.nn.functional.linear(torch.from_numpy(z).to(device),model.reconstruction.weight[-54:-36],model.reconstruction.bias[-54:-36])
    p=structured(code*model.reconstruction_scale+model.reconstruction_mean).cpu()
    t=structured(torch.from_numpy(target));raw_p=inverse(p).numpy();raw_t=inverse(t).numpy();log_p=p.numpy();log_t=t.numpy()
    if raw_target is not None:
        raw_t=np.asarray(raw_target,dtype=np.float64);valid=np.asarray(source_valid,dtype=bool)
        log_t=np.zeros_like(raw_t)
        for j in PRICES:log_t[:,j]=np.log(np.where(valid[:,j],raw_t[:,j],1))
        for j in ACTIVITY:log_t[:,j]=np.log1p(np.where(valid[:,j],raw_t[:,j],0))
    if not np.isfinite(raw_p).all():raise FloatingPointError('Nonfinite anchor prediction')
    result=[]
    for j in range(18):
        keep=valid[:,j];a=raw_p[keep,j];b=raw_t[keep,j];den=np.abs(b);positive=den>1e-12
        result.append(dict(channel=j,count=int(keep.sum()),raw_mae=float(np.abs(a-b).mean()),
            log_mae=float(np.abs(log_p[keep,j]-log_t[keep,j]).mean()),relative_mean=float((np.abs(a-b)[positive]/den[positive]).mean()) if positive.any() else None,
            zero_denominators=int((den==0).sum()),tiny_denominators=int(((den>0)&~positive).sum())))
    return dict(channels=result,price_relative_mean=float(np.mean([result[j]['relative_mean'] for j in PRICES])),activity_log_mae=float(np.mean([result[j]['log_mae'] for j in ACTIVITY])))


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--data',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True);p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    a=p.parse_args();a.resume=False;preflight(a);started=time.time();torch.set_num_threads(8);torch.set_float32_matmul_precision('highest')
    state=torch.load(a.checkpoint,map_location='cpu',weights_only=False);identity=state['identity'];torch.manual_seed(identity['seed'])
    rows,manifest=load(a.data,('train','development','novel_development'))
    if identity['dataset_hash']!=manifest['hash']:raise ValueError('Dataset mismatch')
    c=Config(**identity['config'])
    if c.anchor_mode!='learned':raise ValueError('Embedding-only model required')
    model=JointModel(c,**{k:torch.tensor(v) for k,v in identity['parameters'].items()}).to(a.device)
    model.encoder.load_state_dict({k.removeprefix('encoder.'):v for k,v in state['model'].items() if k.startswith('encoder.')})
    model.reconstruction.load_state_dict({k.removeprefix('reconstruction.'):v for k,v in state['model'].items() if k.startswith('reconstruction.')})
    extracted={}
    for name,r in rows.items():
        write_json(a.output/'progress.json',dict(stage='encoding '+name,origins=len(r),elapsed=time.time()-started))
        extracted[name]=features(model,cache(r),a.device,started+600)
    def report():return {k:evaluate(model,v,rows[k]['context'][:,7]['values'][:,-1],rows[k]['context'][:,7]['valid'][:,-1]) for k,v in extracted.items()}
    before=report();write_json(a.output/'progress.json',dict(stage='fitting training-only linear readout',elapsed=time.time()-started))
    calibration=fit(model,extracted['train']);after=report()
    for key,value in model.encoder.state_dict().items():torch.testing.assert_close(value.cpu(),state['model']['encoder.'+key],rtol=0,atol=0)
    if time.time()>started+600:raise TimeoutError('Anchor fit deadline')
    identity={**identity,'source_revision':a.source_revision,'initialization_type':'Frozen parent encoder, training-fitted anchor, fresh temporal network',
        'parent_checkpoint_sha256':file_hash(a.checkpoint),'anchor_fit':calibration}
    torch.save(dict(model=model.state_dict(),identity=identity,step=0,initialization_only=True),a.output/'initialization.pt')
    write_json(a.output/'results.json',dict(state='complete',source_revision=a.source_revision,parent_checkpoint_sha256=file_hash(a.checkpoint),
        initialization_sha256=file_hash(a.output/'initialization.pt'),before=before,after=after,calibration=calibration,seconds=time.time()-started,encoder_parent_exact_match=True,
        policy='Readout diagnostic/initialization only, not a forecasting result. Encoder unchanged. No optimizer state; cannot engineering-resume this checkpoint.'))
    write_json(a.output/'progress.json',dict(stage='complete',elapsed=time.time()-started))


if __name__=='__main__':main()
