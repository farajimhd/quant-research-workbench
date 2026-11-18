"""Frozen future-information probes; labels are observed future bars, never inputs."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
import numpy as np
import torch
from types import SimpleNamespace
from sklearn.linear_model import Ridge
from .data import load,cache,anchor
from .model import Config,JointModel,structured
from ..phase2_v1.data import sealed
from ..phase1_v8.runtime import preflight
from ..phase1_v8.model import ACTIVITY
from ..v5.shards.phase1_contract import write_json,file_hash


def labels(d):
    y=d['target'].numpy();v=d['target_valid'].numpy();past=structured(anchor(d['x'],d['valid'])).numpy()
    names=['close_return_1','close_return_16','close_return_64']
    values=[y[:,h-1,3]-past[:,3] for h in (1,16,64)]
    masks=[v[:,h-1,3] for h in (1,16,64)]
    for j in ACTIVITY:
        names.append('mean_log_activity_change_'+str(j));values.append(y[:,:,j].mean(1)-past[:,j]);masks.append(v[:,:,j].all(1))
    names+=['future_close_realized_volatility','future_trade_log_range']
    values += [np.sqrt(np.mean(np.diff(y[:,:,3],axis=1)**2,axis=1)),y[:,:,1].max(1)-y[:,:,2].min(1)]
    masks += [v[:,:,3].all(1),v[:,:,[1,2]].all((1,2))]
    return names,np.stack(values,1),np.stack(masks,1)


def summaries(d):
    x=d['x'][...,:18];v=d['valid'][...,:18];n=v.sum(2).clamp_min(1)
    mean=torch.where(v,x,0).sum(2)/n
    variance=torch.where(v,(x-mean[:,:,None]).square(),0).sum(2)/n
    first=torch.where(v[:,:,0],x[:,:,0],mean);last=torch.where(v[:,:,-1],x[:,:,-1],mean)
    return torch.cat((mean,variance.sqrt(),last-first,last,v.float().mean(2)),2).flatten(1).numpy()


def window_labels(d):
    x=d['x'].reshape(-1,64,27);v=d['valid'].reshape_as(x)
    names=['log_price_scale','log_activity_scale','window_log_return','return_volatility','log_range','log_quote_spread','log_trade_count']
    def average(value,mask):return torch.where(mask,value,0).sum(1)/mask.sum(1).clamp_min(1)
    returns=torch.diff(x[...,3],dim=1);rv=v[:,1:,3]&v[:,:-1,3];quotes=v[...,9]&v[...,15]
    values=[average(x[...,3],v[...,3]),average(x[...,4],v[...,4]),x[:,-1,3]-x[:,0,3],
        average(returns.square(),rv).sqrt(),torch.where(v[...,1],x[...,1],-float('inf')).amax(1)-torch.where(v[...,2],x[...,2],float('inf')).amin(1),
        average(x[...,15]-x[...,9],quotes),average(x[...,5],v[...,5])]
    masks=[v[...,3].any(1),v[...,4].any(1),v[:,-1,3]&v[:,0,3],rv.any(1),v[...,1].any(1)&v[...,2].any(1),quotes.any(1),v[...,5].any(1)]
    return names,torch.stack(values,1).numpy(),torch.stack(masks,1).numpy()


@torch.no_grad()
def embeddings(model,d,device='cpu'):
    model.eval();result=[];windows=[]
    for start in range(0,len(d['x']),128):
        o=model(d['x'][start:start+128].to(device),d['valid'][start:start+128].to(device),d['ages'][start:start+128].to(device),reconstruct=False)
        result.append(o['joint'].cpu().numpy())
        windows.append(o['windows'].flatten(1).cpu().numpy())
    return dict(joint=np.concatenate(result),windows=np.concatenate(windows))


def fit_evaluate(features,targets):
    train=features['train'];mean=train.mean(0);std=train.std(0);std=np.where(std>1e-7,std,1)
    x={k:(v-mean)/std for k,v in features.items()};names,y,valid=targets['train'];result={}
    groups={}
    for j in range(len(names)):groups.setdefault(valid[:,j].tobytes(),[]).append(j)
    predictions={k:np.zeros((len(v),len(names))) for k,v in features.items()}
    for ids in groups.values():
        keep=valid[:,ids[0]];model=Ridge(alpha=100.,solver='cholesky').fit(x['train'][keep],y[keep][:,ids])
        for split in features:predictions[split][:,ids]=model.predict(x[split]).reshape(len(x[split]),len(ids))
    for j,name in enumerate(names):
        keep=valid[:,j];baseline=float(y[keep,j].mean())
        report={}
        for split in features:
            _,truth,mask=targets[split];ix=mask[:,j];t=truth[ix,j];p=predictions[split][ix,j];err=p-t
            report[split]=dict(count=int(ix.sum()),mae=float(np.abs(err).mean()),rmse=float(np.sqrt(np.mean(err**2))),
                r2=float(1-np.mean(err**2)/max(np.var(t),1e-20)),training_mean_mae=float(np.abs(t-baseline).mean()),
                zero_mae=float(np.abs(t).mean()),prediction_variance=float(np.var(p)),target_variance=float(np.var(t)))
        result[name]=report
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--runs',nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--data',type=Path)
    p.add_argument('--device',choices=('cpu','cuda'),default='cpu');p.add_argument('--source-revision');a=p.parse_args();torch.set_num_threads(8)
    if a.output.exists():raise FileExistsError('Preserve immutable probe report')
    if a.device=='cuda':
        if not a.source_revision:raise ValueError('GPU probes require immutable source identity')
        preflight(SimpleNamespace(output=a.output.parent/('owner-'+a.output.stem),device='cuda',resume=False,source_revision=a.source_revision))
    torch.set_float32_matmul_precision('highest')
    rows,manifest=load(a.data or a.root/'dataset',('train','development','novel_development'))
    data={k:cache(v) for k,v in rows.items()};targets={k:labels(v) for k,v in data.items()}
    retention={k:window_labels(v) for k,v in data.items()}
    raw={k:summaries(v) for k,v in data.items()}
    result=dict(dataset_hash=manifest['hash'],device=a.device,source_revision=a.source_revision,
        precision='Configured CUDA autocast' if a.device=='cuda' else 'CPU FP32 screening',method='Frozen encoder. Ridge alpha=100, standardized using training only, no tuning on validation. All eligible future labels, rejection counts explicit.',
        labels=targets['train'][0],raw_summary=dict(features=raw['train'].shape[1],metrics=fit_evaluate(raw,targets),
            window_retention=fit_evaluate({k:v.reshape(-1,90) for k,v in raw.items()},retention)),runs={})
    for name in a.runs:
        cp=a.root/name/'best.pt';state=torch.load(cp,map_location='cpu',weights_only=False);identity=state['identity']
        if identity['dataset_hash']!=manifest['hash']:
            expanded=sealed(a.root/'dataset-expanded'/'manifest.json')
            if expanded['hash']!=identity['dataset_hash'] or expanded.get('expansion',{}).get('base_hash')!=manifest['hash']:raise ValueError('Dataset mismatch: require byte-verified nested probe training and identical holdouts')
        c=Config(**identity['config']);parameters={k:torch.tensor(v) for k,v in identity['parameters'].items()}
        torch.manual_seed(identity['seed']);model=JointModel(c,**parameters).to(a.device)
        untrained={k:embeddings(model,d,a.device) for k,d in data.items()};model.load_state_dict(state['model'])
        trained={k:embeddings(model,d,a.device) for k,d in data.items()}
        result['runs'][name]=dict(checkpoint_sha256=file_hash(cp),features=c.embedding,window_features=16*c.embedding,encoder_training_dataset_hash=identity['dataset_hash'],
            untrained=fit_evaluate({k:v['joint'] for k,v in untrained.items()},targets),trained=fit_evaluate({k:v['joint'] for k,v in trained.items()},targets),
            untrained_windows=fit_evaluate({k:v['windows'] for k,v in untrained.items()},targets),trained_windows=fit_evaluate({k:v['windows'] for k,v in trained.items()},targets),
            untrained_retention=fit_evaluate({k:v['windows'].reshape(-1,c.embedding) for k,v in untrained.items()},retention),
            trained_retention=fit_evaluate({k:v['windows'].reshape(-1,c.embedding) for k,v in trained.items()},retention))
        print(name,flush=True)
    result['label_eligibility']={k:{'total':len(v[1]),'valid':v[2].sum(0).tolist(),'rejected':(~v[2]).sum(0).tolist()} for k,v in targets.items()}
    write_json(a.output,result)


if __name__=='__main__':main()
