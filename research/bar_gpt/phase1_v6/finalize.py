"""Single sealed audit pass for at most two preselected finalists."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json,time,shutil
from pathlib import Path
import numpy as np
import torch
from .model import Config,Autoencoder
from .train import load_data,cache,predict
from .metrics import evaluate,probes,summaries
from .export import save_encoder,load_encoder
from ..v5.shards.phase1_contract import file_hash,write_json


def rank_diagnostics(z):
    x=z-z.mean(0)
    covariance=x.T@x/max(len(x)-1,1)
    eigen=np.linalg.eigvalsh(covariance.astype(np.float64)).clip(0)
    if eigen.sum()==0:
        return dict(effective_rank=0.,variance=z.var(0).tolist(),constant_dimensions=z.shape[1],largest_component_fraction=0.,variance_trace=0.)
    mass=eigen/eigen.sum()
    positive=mass>0
    return dict(effective_rank=float(np.exp(-(mass[positive]*np.log(mass[positive])).sum())),
        variance=z.var(0).tolist(),constant_dimensions=int((z.std(0)<1e-7).sum()),
        largest_component_fraction=float(mass[-1]),variance_trace=float(eigen.sum()))


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--names',required=True);p.add_argument('--device',default='cpu');a=p.parse_args()
    names=a.names.split(',');assert 1<=len(names)<=2 and len(set(names))==len(names)
    output=a.root/'finalist-audit'
    if output.exists():raise FileExistsError('Audit is sealed; repeated consultation requires a new explicit decision')
    output.mkdir()
    started=time.time();torch.set_num_threads(4)
    # Freeze selection BEFORE opening held-out values or evaluating any finalist.
    selections=[]
    for name in names:
        run=a.root/name;manifest=json.loads((run/'manifest.json').read_text())
        receipt=json.loads((a.root/f"deployment-{manifest['source_revision'][:9]}.json").read_text())
        assert receipt['revision']==manifest['source_revision']
        result=json.loads((run/'results.json').read_text());assert result['state']=='complete'
        assert file_hash(run/'best.pt')==result['checkpoint_sha256']
        selections.append(dict(name=name,checkpoint_sha256=result['checkpoint_sha256'],source_revision=manifest['source_revision']))
    write_json(output/'selection.json',dict(selected=selections,time=time.time(),policy='Selected only from development results; audit cannot tune architecture or select checkpoints'))
    rows,manifest=load_data(a.root/'dataset')
    audit_path=a.root/'dataset/audit.npy';assert file_hash(audit_path)==manifest['files']['audit.npy']
    rows['audit']=np.load(audit_path,allow_pickle=False)
    results={}
    for name in names:
        run=a.root/name;state=torch.load(run/'best.pt',map_location='cpu',weights_only=False)
        identity=state['identity'];config=Config(**identity['config'])
        mean=torch.tensor(identity['calibration']['mean']);scale=torch.tensor(identity['calibration']['scale'])
        data={key:cache(value,mean,scale,a.device) for key,value in rows.items() if key in ('train','development','audit')}
        model=Autoencoder(config,mean,scale).to(a.device);model.load_state_dict(state['model'])
        bs=512;metrics={};embeddings={}
        for partition in ('train','development','audit'):
            for full in (True,False):
                pred,z,mask=predict(model,data[partition],bs,full)
                metrics[partition+('_full' if full else '_masked')]=evaluate(pred,rows[partition],mask)
                if full:embeddings[partition]=z
        info=probes(embeddings['train'],embeddings['audit'],rows['train'],rows['audit'])
        torch.manual_seed(identity['seed'])
        untrained=Autoencoder(config,mean,scale).to(a.device)
        _,train_z,_=predict(untrained,data['train'],bs)
        _,audit_z,_=predict(untrained,data['audit'],bs)
        sy,_=summaries(rows['train']);ay,_=summaries(rows['audit'])
        result=dict(identity=identity,checkpoint_sha256=file_hash(run/'best.pt'),selected_step=state['step'],
            dataset_hash=manifest['hash'],metrics=metrics,audit_probes=info,
            untrained_audit_probes=probes(train_z,audit_z,rows['train'],rows['audit']),
            raw_summary_audit_probes=probes(sy,ay,rows['train'],rows['audit']),
            representation=rank_diagnostics(embeddings['audit']),precision='FP32 model, FP64 raw inverse',
            inference_contract='encoder returns fixed vector; model.decode(vector) and model.transform.inverse(output) require no target references')
        write_json(output/(name+'.json'),result);results[name]=result
        shutil.copy2(run/'best.pt',output/(name+'-pinned.pt'))
        assert file_hash(output/(name+'-pinned.pt'))==result['checkpoint_sha256']
        encoder_path=output/(name+'-encoder-only.pt')
        save_encoder(model,encoder_path,checkpoint_sha256=result['checkpoint_sha256'],source_revision=identity['source_revision'],dataset_hash=manifest['hash'])
        exported=load_encoder(encoder_path,a.device)
        sample=rows['audit'][:8]
        with torch.no_grad():
            actual=exported.encode_raw(torch.from_numpy(sample['values'].copy()).to(a.device),
                torch.from_numpy(sample['valid'].astype(bool)).to(a.device),torch.from_numpy(sample['end_us'].copy()).to(a.device),
                torch.from_numpy(sample['timeframe_us'].copy()).to(a.device))
        assert not hasattr(exported,'decoder')
        torch.testing.assert_close(actual.cpu(),torch.from_numpy(embeddings['audit'][:8]),rtol=1e-5,atol=1e-5)
        write_json(output/(name+'-export.json'),dict(sha256=file_hash(encoder_path),dimensions=config.embedding,
            decoder_present=False,raw_input_parity=True,checkpoint_sha256=result['checkpoint_sha256']))
        del exported
        del model,untrained,data
        if a.device=='cuda':torch.cuda.empty_cache()
    write_json(output/'complete.json',dict(names=names,elapsed_seconds=time.time()-started,time=time.time(),state='complete',dataset_hash=manifest['hash']))


if __name__=='__main__':main()
