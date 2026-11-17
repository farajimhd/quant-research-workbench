"""Compare pinned previous candidates on the new audit after selection is sealed."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
import numpy as np
import torch
from ..phase1_v6.model import Autoencoder,Config
from ..phase1_v6.train import cache,predict
from ..phase1_v6.metrics import evaluate,probes
from .nonlinear_probe import probe
from ..v5.shards.phase1_contract import file_hash,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--previous',type=Path,required=True)
    p.add_argument('--device',default='cpu');a=p.parse_args()
    assert (a.root/'finalist-audit/selection.json').exists(),'Freeze current finalists first'
    output=a.root/'previous-on-new-audit.json'
    if output.exists():raise FileExistsError(output)
    new=json.loads((a.root/'dataset/manifest.json').read_text());old=json.loads((a.previous/'dataset/manifest.json').read_text())
    assert new['parent_hash']==old['hash']
    for name in ('train.npy','development.npy'):assert new['files'][name]==old['files'][name]
    rows={}
    for name in ('train','audit'):
        path=a.root/'dataset'/f'{name}.npy';assert file_hash(path)==new['files'][path.name]
        rows[name]=np.load(path,allow_pickle=False)
    started=time.time();torch.set_num_threads(4);results={}
    for name in ('e06-wide-embedding','e08-token-validity'):
        checkpoint=a.previous/'finalist-audit'/f'{name}-pinned.pt'
        prior=json.loads((a.previous/'finalist-audit'/f'{name}.json').read_text())
        assert file_hash(checkpoint)==prior['checkpoint_sha256']
        state=torch.load(checkpoint,map_location='cpu',weights_only=False);identity=state['identity']
        mean=torch.tensor(identity['calibration']['mean']);scale=torch.tensor(identity['calibration']['scale'])
        model=Autoencoder(Config(**identity['config']),mean,scale).to(a.device);model.load_state_dict(state['model'])
        data={k:cache(v,mean,scale,a.device) for k,v in rows.items()}
        _,train_z,_=predict(model,data['train'],512);metrics={};audit_z=None
        for full in (True,False):
            pred,z,mask=predict(model,data['audit'],512,full)
            metrics['full' if full else 'masked']=evaluate(pred,rows['audit'],mask)
            if full:audit_z=z
        results[name]=dict(checkpoint_sha256=file_hash(checkpoint),source_revision=identity['source_revision'],
            original_dataset_hash=old['hash'],audit_dataset_hash=new['hash'],metrics=metrics,
            audit_probes=probes(train_z,audit_z,rows['train'],rows['audit']),
            nonlinear_audit_probes=probe(train_z,audit_z,rows['train'],rows['audit'],a.device))
        del model,data
        if a.device=='cuda':torch.cuda.empty_cache()
    write_json(output,dict(results=results,seconds=time.time()-started,policy='Prior pinned winners only; no retraining or audit-based checkpoint selection'))


if __name__=='__main__':main()
