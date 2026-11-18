"""Sealed finalist evaluation and standalone learned-window encoder export."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
import torch
import numpy as np
from .data import load,cache
from .model import Config,JointModel,WindowEncoder,EmbeddingForecaster
from .metrics import measure,masked_reconstruction,reconstruction_controls
from ..phase1_v8.runtime import preflight
from ..v5.shards.phase1_contract import write_json,file_hash
from ..phase2_v1.data import sealed
from ..phase1_v8.data import cache as window_cache
from ..phase1_v8.model import inverse
from ..phase1_v7.metrics import evaluate as native_metrics,require_structured


@torch.no_grad()
def phase1_transfer(model,root,device):
    manifest=sealed(root/'manifest.json');path=root/'audit.npy'
    if file_hash(path)!=manifest['files'][path.name]:raise ValueError('Phase 1 audit changed')
    rows=np.load(path,mmap_mode='r',allow_pickle=False);result=dict(dataset_hash=manifest['hash'],
        scope='Cross-contract reconstruction only. The joint training contract has five timeframes; 100ms is zero-shot transfer, not trained Phase 1 coverage.')
    for masked in (False,True):
        rng=np.random.default_rng(917);pred=[]
        for start in range(0,len(rows),256):
            d=window_cache(rows[start:start+256],device);x=d['values'];v=d['valid']
            if masked:v=v&torch.from_numpy(rng.random(v.shape[:-1]).argsort(-1).argsort(-1)<16).to(device)[...,None]
            pred.append(inverse(model.reconstruct(model.encoder(x,v))[0]).cpu().numpy())
        metrics=native_metrics(np.concatenate(pred),rows);require_structured(metrics,paired=True)
        result['masked16' if masked else 'full']=metrics
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--run',required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--audit',action='store_true');p.add_argument('--device',default='cuda',choices=('cpu','cuda'));p.add_argument('--data',type=Path)
    p.add_argument('--phase1-data',type=Path)
    a=p.parse_args();a.resume=False;preflight(a);torch.set_num_threads(8);started=time.time()
    if a.phase1_data and not a.audit:raise ValueError('Cross-contract audit requires explicit finalist audit')
    cp=a.root/a.run/'best.pt';state=torch.load(cp,map_location=a.device,weights_only=False);identity=state['identity']
    partitions=('audit_2025','audit_2026') if a.audit else ('development','novel_development')
    dataset=a.data or a.root/'dataset';rows,manifest=load(dataset,partitions)
    if manifest['hash']!=identity['dataset_hash']:raise ValueError('Dataset identity mismatch')
    config=Config(**identity['config']);parameters={k:torch.tensor(v) for k,v in identity['parameters'].items()}
    model=JointModel(config,**parameters).to(a.device);model.load_state_dict(state['model']);model.eval()
    report=dict(run=a.run,checkpoint_sha256=file_hash(cp),source_revision=a.source_revision,dataset_hash=manifest['hash'],evaluations={})
    for partition,r in rows.items():
        d={k:v.to(a.device) for k,v in cache(r).items()};identities=json.loads((dataset/(partition+'-identities.json')).read_text())
        begin=time.time();report['evaluations'][partition]=measure(model,d,r,identities)
        report['evaluations'][partition]['masked_reconstruction']=masked_reconstruction(model,d,r)
        report['evaluations'][partition]['reconstruction_controls']=reconstruction_controls(model,d,r)
        report['evaluations'][partition]['seconds']=time.time()-begin
        print(partition,flush=True)
    package=dict(version='joint-v9-window-encoder-1',config=identity['config'],state={k:v.cpu() for k,v in model.encoder.state_dict().items()},
        input_mean=model.input_mean.cpu(),input_scale=model.input_scale.cpu(),checkpoint_sha256=file_hash(cp),identity=identity,
        contract='Input log/log1p 18 market + 9 time features and 27 source-valid flags, shape [N,64,27]. Output learned fixed-size window embedding. No decoder or copied reference fields.')
    standalone=WindowEncoder(config,package['input_mean'],package['input_scale']).to(a.device);standalone.load_state_dict(package['state']);standalone.eval()
    x=d['x'][:2].reshape(-1,64,27);v=d['valid'][:2].reshape(-1,64,27)
    with torch.no_grad():torch.testing.assert_close(standalone(x,v),model.encoder(x,v),rtol=0,atol=0)
    torch.save(package,a.output/'window-encoder.pt');report['encoder_sha256']=file_hash(a.output/'window-encoder.pt')
    if config.anchor_mode=='learned':
        forecaster=EmbeddingForecaster.from_model(model).eval()
        with torch.no_grad():
            z=model.encode_windows(d['x'][:2],d['valid'][:2]);ages=d['ages'][:2]
            torch.testing.assert_close(forecaster(z,ages)['forecast'],model.forecast_from_embeddings(z,ages)['forecast'],rtol=0,atol=0)
        phase2=dict(version='joint-v9-embedding-forecaster-1',config=identity['config'],
            calibration={k:getattr(model,k).cpu() for k in ('reconstruction_mean','reconstruction_scale','forecast_scale')},
            state={k:v.cpu() for k,v in forecaster.state_dict().items()},checkpoint_sha256=file_hash(cp),
            contract='Input 16 learned window embeddings and 16x2 causal ages only. No raw bars, no true reference, no Phase 1 reconstruction decoder. Apply fixed inverse(logs) for 18 raw-unit features.')
        torch.save(phase2,a.output/'embedding-forecaster.pt');report['forecaster_sha256']=file_hash(a.output/'embedding-forecaster.pt')
    if a.phase1_data:report['phase1_transfer']=phase1_transfer(model,a.phase1_data,a.device)
    report.update(elapsed_seconds=time.time()-started,export_parity='exact on final partition examples',state='complete')
    write_json(a.output/'results.json',report)


if __name__=='__main__':main()
