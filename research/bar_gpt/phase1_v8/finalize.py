"""Pinned-checkpoint reproduction and sealed finalist audit; no model updates."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json,time
from pathlib import Path
import numpy as np
import torch
from .model import Config,Autoencoder,inverse
from .data import load_data,cache
from .evaluate import predict,measure,evaluate,probes,summaries
from .export import save,load
from .runtime import preflight
from ..phase1_v7.nonlinear_probe import probe
from ..v5.shards.phase1_contract import file_hash,write_json


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True);p.add_argument('--runs',nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--partition',choices=('development','audit'),default='development')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda');p.add_argument('--nonlinear',action='store_true')
    a=p.parse_args();a.resume=False;started=time.time();preflight(a)
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest');torch.use_deterministic_algorithms(True)
    rows,manifest=load_data(a.root/'dataset')
    identities={}
    for name in a.runs:
        path=a.root/name
        result=json.loads((path/'results.json').read_text())
        checkpoint=path/'best.pt'
        if result['state']!='complete' or file_hash(checkpoint)!=result['checkpoint_sha256']:raise ValueError('Unverified finalist')
        identities[name]=dict(checkpoint=str(checkpoint),sha256=result['checkpoint_sha256'])
    # Written before reading any held-out values; the candidate set is now frozen.
    write_json(a.output/'selection.json',dict(runs=identities,dataset_hash=manifest['hash'],partition=a.partition,
        protocol='Fixed batch 1024/mask seed 731; frozen linear and optional fixed-budget nonlinear probes; no audit tuning',
        audit_limitation=manifest.get('policy'),started=started))
    if a.partition=='audit':
        path=a.root/'dataset'/'audit.npy'
        if file_hash(path)!=manifest['files']['audit.npy']:raise ValueError('Audit changed')
        rows['audit']=np.load(path,allow_pickle=False)
    data={k:cache(rows[k],a.device) for k in ('train',a.partition)}
    out={}
    for name,identity in identities.items():
        if time.time()-started>1200:raise TimeoutError('Finalist evaluation budget exceeded')
        state=torch.load(identity['checkpoint'],map_location=a.device,weights_only=False);m=state['identity']
        if m['dataset_hash']!=manifest['hash']:raise ValueError('Data identity mismatch')
        params={k:torch.tensor(v) for k,v in m['parameters'].items()}
        torch.manual_seed(m['seed']);model=Autoencoder(Config(**m['config']),**params).to(a.device).eval()
        _,ut,_,_=predict(model,data['train'],1024)
        _,ue,_,_=predict(model,data[a.partition],1024)
        result=dict(untrained_linear=probes(ut,ue,rows['train'],rows[a.partition]))
        if a.nonlinear:result['untrained_nonlinear']=probe(ut,ue,rows['train'],rows[a.partition],a.device)
        del ut,ue
        model.load_state_dict(state['model']);del state
        _,tz,_,_=predict(model,data['train'],1024)
        result['full'],ez=measure(model,data[a.partition],rows[a.partition],1024,True)
        result['masked'],_=measure(model,data[a.partition],rows[a.partition],1024,False)
        constant=inverse(model.input_mean[:18].expand(1,64,18)).cpu().numpy()
        result['training_constant_full']=evaluate(np.broadcast_to(constant,(len(rows[a.partition]),64,18)),rows[a.partition])
        _,_,hidden,_=predict(model,data[a.partition],1024,False)
        visible=rows[a.partition]['valid'].astype(bool)&~hidden
        control=np.empty_like(rows[a.partition]['values'],dtype=np.float64)
        for j in range(18):
            med=np.ma.median(np.ma.array(rows[a.partition]['values'][...,j],mask=~visible[...,j]),axis=1).filled(float(constant[0,0,j]))
            control[...,j]=med[:,None]
        result['visible_median_hidden']=evaluate(control,rows[a.partition],hidden)
        del control,hidden,visible
        result['trained_linear']=probes(tz,ez,rows['train'],rows[a.partition])
        if a.nonlinear:result['trained_nonlinear']=probe(tz,ez,rows['train'],rows[a.partition],a.device)
        sy,_=summaries(rows['train']);ey,_=summaries(rows[a.partition])
        result['raw_summary_upper_bound']=probes(sy,ey,rows['train'],rows[a.partition])
        # Recovery through the disposable decoder, not a copied/reference latent slot.
        prediction,_,_,_=predict(model,data[a.partition],1024)
        decoded=rows[a.partition].copy();decoded['values']=prediction
        recovered,names=summaries(decoded)
        result['decoder_summary_mae']={key:float(np.abs(recovered[:,j]-ey[:,j]).mean()) for j,key in enumerate(names)}
        artifact=a.output/(name+'-encoder.pt');save(model,artifact,**identity,dataset_hash=manifest['hash'])
        encoder=load(artifact,a.device)
        from .data import batch
        gen=torch.Generator(device=a.device).manual_seed(731)
        b=batch(data[a.partition],torch.arange(min(32,len(ez)),device=a.device),model.config.visible,gen,True)
        with torch.no_grad():torch.testing.assert_close(encoder.encode(*b[:3]),model.encode(*b[:3]),rtol=0,atol=0)
        result.update(encoder_sha256=file_hash(artifact),encoder_parameters=sum(v.numel() for v in encoder.parameters()),
            checkpoint=identity,encoder_export_exact_parity=True)
        write_json(a.output/(name+'.json'),result);out[name]=result
        del model,encoder,tz,ez,prediction,decoded
    write_json(a.output/'results.json',dict(state='complete',elapsed_seconds=time.time()-started,partition=a.partition,runs=out))
    print(json.dumps(dict(state='complete',runs=list(out),seconds=time.time()-started)))


if __name__=='__main__':main()
